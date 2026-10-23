"""
test_pr_publisher.py -- hermetic tests for the goose -> GitHub-PR bridge.

The git/GitHub seam (gitops.py). The publish POLICY that used to be tested here
through Publisher.run_once (watermark, skips) was retired by RCA 2026-10 Fix 3;
its surviving rules are tested against the producer in
tests/test_producer_commit.py.
"""
from __future__ import annotations

import json
import tempfile
import random
import types

from zo_sentinel.publisher.gitops import (
    CliGitOps,
    _is_rate_limited,
    _is_transient_net,
)


def test_is_rate_limited_markers():
    assert _is_rate_limited("You have exceeded a secondary rate limit")
    assert _is_rate_limited("API rate limit exceeded for user")
    assert not _is_rate_limited("fatal: not a git repository")
    assert not _is_rate_limited("")


def test_gitops_backs_off_then_succeeds():
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        if calls["n"] < 3:
            return types.SimpleNamespace(
                returncode=1, stderr="You have exceeded a secondary rate limit", stdout="")
        return types.SimpleNamespace(returncode=0, stderr="", stdout="ok")

    slept = []
    g = CliGitOps("/tmp/nope", sleep=lambda s: slept.append(s), rng=random.Random(0))
    r = g._run_with_backoff(fake)
    assert r.returncode == 0 and calls["n"] == 3
    assert len(slept) == 2          # backed off twice before the 3rd success


def test_is_transient_net_markers():
    assert _is_transient_net("fatal: unable to access '...': Send failure: Broken pipe")
    assert _is_transient_net("Connection reset by peer")
    assert _is_transient_net("Could not resolve host: github.com")
    assert not _is_transient_net("fatal: not a git repository")
    assert not _is_transient_net("")


def test_gitops_backs_off_on_transient_net_then_succeeds():
    """A broken pipe on a git step is a transient blip -> back off + retry, never
    a hard break. Regression for the post-deploy 'Send failure: Broken pipe' on
    `git fetch` that failed a whole publish cycle."""
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        if calls["n"] < 2:
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="fatal: unable to access '...': Send failure: Broken pipe")
        return types.SimpleNamespace(returncode=0, stderr="", stdout="ok")

    slept = []
    g = CliGitOps("/tmp/nope", sleep=lambda s: slept.append(s), rng=random.Random(0))
    r = g._run_with_backoff(fake)
    assert r.returncode == 0 and calls["n"] == 2 and len(slept) == 1


def test_gitops_no_retry_on_ordinary_failure():
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return types.SimpleNamespace(returncode=1, stderr="fatal: some other error", stdout="")

    g = CliGitOps("/tmp/nope", sleep=lambda s: None)
    r = g._run_with_backoff(fake)
    assert r.returncode == 1 and calls["n"] == 1   # no retry on non-rate-limit


def test_cligitops_missing_label_does_not_fail_pr(tmp_path, monkeypatch):
    """A missing GitHub label must NOT fail a PR: create without --label, then
    attach best-effort. Regression guard for 'could not add label ... not found'
    that stuck the publisher (every publish failed, watermark never advanced)."""
    import zo_sentinel.publisher.gitops as gmod

    seen = []

    def fake_run(args, **kw):
        seen.append(list(args))
        if args[:1] == ["git"] and args[3:4] == ["diff"]:   # staged diff present -> proceed to commit
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[:3] == ["gh", "pr", "create"]:
            return types.SimpleNamespace(
                returncode=0, stdout="https://github.com/rob531/zo-sentinel/pull/7\n", stderr="")
        if args[:3] == ["gh", "pr", "edit"]:   # label attach fails (label missing)
            return types.SimpleNamespace(
                returncode=1, stdout="", stderr="could not add label: 'autonomous-build' not found")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")   # git steps, label create

    monkeypatch.setattr(gmod.subprocess, "run", fake_run)
    g = gmod.CliGitOps(str(tmp_path), sleep=lambda *_: None)
    plan = gmod.PublishPlan(branch="auto/build/x", title="t", body="b", file_path="x.py",
                            content="print(1)\n", dedup_key="k",
                            labels=["autonomous-build", "ladder:builder_low"])
    res = g.publish(plan)

    assert res.ok is True                                   # PR succeeds despite label failure
    assert res.pr_url == "https://github.com/rob531/zo-sentinel/pull/7"
    create = next(c for c in seen if c[:3] == ["gh", "pr", "create"])
    assert "--label" not in create                          # labels NOT on the create call


def test_cligitops_already_exists_is_success(tmp_path, monkeypatch):
    """A branch that already has a PR is idempotent SUCCESS (recover its URL),
    not a failure -- otherwise a dropped state-write stalls the publisher forever
    re-attempting an already-open PR and the watermark never advances."""
    import zo_sentinel.publisher.gitops as gmod

    def fake_run(args, **kw):
        if args[:1] == ["git"] and args[3:4] == ["diff"]:   # staged diff present -> proceed
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[:3] == ["gh", "pr", "create"]:
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr='a pull request for branch "auto/build/x" into "main" already exists')
        if args[:3] == ["gh", "pr", "view"]:
            return types.SimpleNamespace(
                returncode=0, stdout="https://github.com/rob531/zo-sentinel/pull/9\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gmod.subprocess, "run", fake_run)
    g = gmod.CliGitOps(str(tmp_path), sleep=lambda *_: None)
    plan = gmod.PublishPlan(branch="auto/build/x", title="t", body="b", file_path="x.py",
                            content="c\n", dedup_key="k", labels=[])
    res = g.publish(plan)
    assert res.ok is True and res.detail == "already exists"
    assert res.pr_url == "https://github.com/rob531/zo-sentinel/pull/9"


def test_cligitops_nothing_to_commit_is_noop_success(tmp_path, monkeypatch):
    """An artifact byte-identical to base stages no diff -> `git commit` exits 1
    with 'nothing to commit' on STDOUT (empty stderr -> bare 'git commit failed').
    That must be an idempotent no-op SUCCESS, never a hard failure -- the bug that
    head-of-line blocked every PR behind a rebuilt-identical OPERATIONS.md."""
    import zo_sentinel.publisher.gitops as gmod

    seen = []

    def fake_run(args, **kw):
        seen.append(list(args))
        # args = ["git", "-C", dir, <subcmd>, ...] for _git; gh otherwise
        sub = args[3] if args[:1] == ["git"] else None
        if sub == "diff":                      # `git diff --cached --quiet` -> 0 = no staged changes
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if sub == "commit":                    # would be "nothing to commit", but we must NOT reach it
            return types.SimpleNamespace(returncode=1, stdout="nothing to commit, working tree clean", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gmod.subprocess, "run", fake_run)
    g = gmod.CliGitOps(str(tmp_path), sleep=lambda *_: None)
    plan = gmod.PublishPlan(branch="auto/build/x", title="t", body="b", file_path="x.py",
                            content="already-on-base\n", dedup_key="k", labels=[])
    res = g.publish(plan)
    assert res.ok is True and res.noop is True
    assert "nothing to commit" in res.detail
    # never attempted to commit, push, or open a PR for a no-op
    assert not any(c[:1] == ["git"] and c[3:4] == ["commit"] for c in seen)
    assert not any(c[:1] == ["git"] and c[3:4] == ["push"] for c in seen)
    assert not any(c[:3] == ["gh", "pr", "create"] for c in seen)


def _artifact2(file, built_at, task):
    """Same as _artifact but with a distinct row id per (file, built_at)."""
    c = {"file": file, "built_at": built_at, "phase": "p1", "bytes": 10,
         "interface": "compute_score", "task": task}
    return (f"row-{file}-{built_at}", json.dumps(c))

# --- anti-hollow pre-publish gate (mirrors tests/ci/no_hollow_scaffold.py) ---

def test_saturation_gate_ignores_non_root_paths():
    from zo_sentinel.publisher.publisher import saturated_family_scan
    assert saturated_family_scan("app/api/fleet_risk_x.py") is None
    assert saturated_family_scan("server_freshness_dashboard_api.py") is None
    assert saturated_family_scan("fleet_risk_composition_api.py") is not None


def test_is_dirty_tree_markers():
    from zo_sentinel.publisher.gitops import _is_dirty_tree
    assert _is_dirty_tree("error: Your local changes to the following files "
                          "would be overwritten by checkout:")
    assert _is_dirty_tree("Please commit your changes or stash them before "
                          "you switch branches.")
    assert not _is_dirty_tree("fatal: not a git repository")
    assert not _is_dirty_tree("")
    assert not _is_dirty_tree(None)


def test_cligitops_selfheals_dirty_clone(tmp_path, monkeypatch):
    """An out-of-band edit inside the pub clone must not wedge publishing:
    checkout fails would-be-overwritten, the publisher stashes the dirt
    (--include-untracked: preserved for forensics, never discarded) and
    retries the checkout ONCE. Regression for 2026-07-13..15: a stray
    working-tree edit deleted the saturated-family gate inside the clone and
    every publish failed identically for ~2 days."""
    import zo_sentinel.publisher.gitops as gmod

    seen = []
    state = {"checkouts": 0}

    def fake_run(args, **kw):
        seen.append(list(args))
        if args[:1] == ["git"] and "checkout" in args:
            state["checkouts"] += 1
            if state["checkouts"] == 1:
                return types.SimpleNamespace(
                    returncode=1, stdout="",
                    stderr="error: Your local changes to the following files "
                           "would be overwritten by checkout:"
                           " zo_sentinel/publisher/publisher.py "
                           "Please commit your changes or stash them before "
                           "you switch branches. Aborting")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:1] == ["git"] and args[3:4] == ["diff"]:   # staged diff present
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[:3] == ["gh", "pr", "create"]:
            return types.SimpleNamespace(
                returncode=0,
                stdout="https://github.com/rob531/zo-sentinel/pull/9",
                stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gmod.subprocess, "run", fake_run)
    g = gmod.CliGitOps(str(tmp_path), sleep=lambda *_: None)
    plan = gmod.PublishPlan(branch="auto/build/x", title="t", body="b",
                            file_path="x.py", content="print(1)", dedup_key="k")
    res = g.publish(plan)

    assert res.ok is True and res.pr_url
    stash = next(c for c in seen if c[:1] == ["git"] and "stash" in c)
    assert "--include-untracked" in stash       # dirt preserved, not discarded
    assert state["checkouts"] == 2              # exactly one retry


def test_cligitops_dirty_tree_still_fails_when_stash_fails(tmp_path, monkeypatch):
    """If the stash itself fails, the publish must fail visibly (no retry
    loop, no silent reset): the cycle reports the checkout error as before."""
    import zo_sentinel.publisher.gitops as gmod

    def fake_run(args, **kw):
        if args[:1] == ["git"] and "checkout" in args:
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="error: ... would be overwritten by checkout: ...")
        if args[:1] == ["git"] and "stash" in args:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="stash failed")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gmod.subprocess, "run", fake_run)
    g = gmod.CliGitOps(str(tmp_path), sleep=lambda *_: None)
    plan = gmod.PublishPlan(branch="auto/build/x", title="t", body="b",
                            file_path="x.py", content="print(1)", dedup_key="k")
    res = g.publish(plan)
    assert res.ok is False
    assert "overwritten" in (res.detail or "")


# --------------------------------------------------------------------------
# FU-209: a path that cannot exist on Windows must never reach a commit.
# Both directions are asserted deliberately. A guard that can ONLY go red is as
# broken as one that can only go green, so the negative control (an ordinary
# path must be accepted) is part of the test, not an afterthought.
# --------------------------------------------------------------------------

def test_portable_path_violation_rejects_windows_reserved_chars():
    from zo_sentinel.publisher.gitops import _portable_path_violation
    # the EXACT path that broke every Windows lane on 2026-07-31
    reason = _portable_path_violation("services/staged/<service_name>/__init__.py")
    assert reason is not None
    assert "FU-209" in reason
    for ch in '<>:"|?*':
        assert _portable_path_violation("services/staged/a%sb/x.py" % ch) is not None


def test_portable_path_violation_accepts_ordinary_paths():
    """NEGATIVE CONTROL. Without this, a guard hard-wired to return a reason
    would pass the test above and silently reject every artifact."""
    from zo_sentinel.publisher.gitops import _portable_path_violation
    for ok in ("services/staged/cve_feed_ingestion/__init__.py",
               "zo_sentinel/publisher/gitops.py",
               "tools/fu/fu_ledger.py",
               "a_b-c.d/e_1.py"):
        assert _portable_path_violation(ok) is None, ok


def test_publisher_refuses_to_commit_unportable_path_permanently():
    """End-to-end at the chokepoint: CliGitOps.publish must bail BEFORE writing
    or staging, and must mark the failure permanent so the queue retires it
    instead of head-of-line-blocking behind an unfixable artifact."""
    import pathlib
    import subprocess
    from zo_sentinel.publisher.gitops import CliGitOps, PublishPlan

    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def _fake_git(*a):
        staged.append(a)
        return ok

    with tempfile.TemporaryDirectory() as d:
        g = CliGitOps(clone_dir=d)
        staged = []
        g._git = _fake_git
        plan = PublishPlan(branch="b", title="t", body="b",
                           file_path="services/staged/<service_name>/__init__.py",
                           content="x\n", dedup_key="k")
        res = g.publish(plan)
        assert res.ok is False
        assert res.permanent is True
        assert "FU-209" in res.detail
        # bailed BEFORE the artifact touched the disk or the index
        assert not list(pathlib.Path(d).rglob("*service_name*"))
        assert not any(a and a[0] == "add" for a in staged)
