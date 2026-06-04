"""
test_pr_publisher.py -- hermetic tests for the goose -> GitHub-PR bridge.

Uses the ingestor's InMemoryMeshStore + a FakeGitOps + an injected content
resolver, so no network, no git, no host. Proves: dormant by default (writes
nothing, plans only), publishes when enabled, dedups across runs, blocks unsafe
artifacts before they ever reach a PR, and carries goose ladder-tier provenance.
"""
from __future__ import annotations

import json
import random
import types

from zo_sentinel.ingestor.store import InMemoryMeshStore
from zo_sentinel.publisher.gitops import (
    CliGitOps,
    FakeGitOps,
    _is_rate_limited,
    _is_transient_net,
)
from zo_sentinel.publisher.publisher import (
    PR_PUBLISHED_TYPE,
    PUBLISHER_AGENT_ID,
    WATERMARK_TYPE,
    Publisher,
)


def _artifact(file, built_at="2026-05-30T00:00:00Z", task="build_x", tier=None):
    c = {"file": file, "built_at": built_at, "phase": "p1", "bytes": 10,
         "interface": "compute_score", "task": task}
    if tier:
        c["tier"] = tier
    return (f"row-{file}", json.dumps(c))


def _pub(store, enabled, content="print('ok')\n", gitops=None, **kw):
    # pr_spacing_sec=0 so tests never sleep; daily_cap high unless overridden.
    kw.setdefault("daily_cap", 50)
    kw.setdefault("pr_spacing_sec", 0)
    return Publisher(
        store,
        gitops=gitops or FakeGitOps(),
        content_resolver=lambda art: content,
        enabled_override=enabled,
        **kw,
    )


def test_dormant_plans_but_publishes_nothing():
    store = InMemoryMeshStore(artifacts=[_artifact("a.py")])
    gitops = FakeGitOps()
    res = _pub(store, enabled=False, gitops=gitops).run_once()
    assert len(res) == 1 and res[0]["action"] == "dry_run"
    assert gitops.published == []          # nothing pushed
    assert store.writes == []              # nothing written to mesh


def test_enabled_publishes_and_records():
    store = InMemoryMeshStore(artifacts=[_artifact("a.py", tier="builder_high")])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops).run_once()
    assert res[0]["action"] == "published"
    assert res[0]["pr_url"].endswith("/pull/FAKE1")
    assert res[0]["tier"] == "builder_high"
    assert len(gitops.published) == 1
    plan = gitops.published[0]
    assert plan.file_path == "a.py"
    assert "ladder:builder_high" in plan.labels and "autonomous-build" in plan.labels
    # recorded the dedup key + an audit row
    pub_rows = store.writes_of_type(PR_PUBLISHED_TYPE)
    assert pub_rows and "a.py|2026-05-30T00:00:00Z" in json.loads(pub_rows[0]["content"])


def test_dedup_skips_already_published():
    store = InMemoryMeshStore(artifacts=[_artifact("a.py")])
    # seed published state
    store.write("mesh_memory", {
        "agent_id": PUBLISHER_AGENT_ID, "memory_type": PR_PUBLISHED_TYPE,
        "content": json.dumps(["a.py|2026-05-30T00:00:00Z"]),
    })
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops).run_once()
    assert res == []                       # nothing new
    assert gitops.published == []


def test_unsafe_artifact_blocked_before_pr():
    store = InMemoryMeshStore(artifacts=[_artifact("evil.py")])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True,
               content="cur.execute('DROP TABLE mcp_server_registry')\n",
               gitops=gitops).run_once()
    assert res[0]["action"] == "blocked"
    assert "mcp_server_registry" in res[0]["detail"]
    assert gitops.published == []          # never reached a PR


def test_unresolved_content_skipped():
    store = InMemoryMeshStore(artifacts=[_artifact("missing.py")])
    pub = Publisher(store, gitops=FakeGitOps(), pr_spacing_sec=0,
                    content_resolver=lambda art: None, enabled_override=True)
    res = pub.run_once()
    assert res[0]["action"] == "skip"


# --- watermark --------------------------------------------------------------

def test_watermark_advances_after_publish():
    store = InMemoryMeshStore(artifacts=[_artifact("a.py", built_at="2026-05-30T00:00:00Z")])
    _pub(store, enabled=True).run_once()
    assert store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) == "2026-05-30T00:00:00Z"


def test_watermark_filters_old_artifacts():
    store = InMemoryMeshStore(artifacts=[
        _artifact("old.py", built_at="2026-05-01T00:00:00Z"),
        _artifact("new.py", built_at="2026-06-01T00:00:00Z"),
    ])
    # seed a watermark between the two -> only new.py is in scope
    store.write("mesh_memory", {"agent_id": PUBLISHER_AGENT_ID,
                                "memory_type": WATERMARK_TYPE,
                                "content": "2026-05-15T00:00:00Z"})
    res = _pub(store, enabled=True).run_once()
    assert [r["file"] for r in res if r["action"] == "published"] == ["new.py"]
    assert store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) == "2026-06-01T00:00:00Z"


def test_dormant_does_not_advance_watermark():
    store = InMemoryMeshStore(artifacts=[_artifact("a.py")])
    _pub(store, enabled=False).run_once()
    assert store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) is None
    assert store.writes == []


# --- daily cap --------------------------------------------------------------

def test_daily_cap_defers_excess_and_pins_watermark():
    arts = [_artifact(f"f{i}.py", built_at=f"2026-05-30T00:0{i}:00Z") for i in range(5)]
    store = InMemoryMeshStore(artifacts=arts)
    res = _pub(store, enabled=True, daily_cap=2).run_once()
    actions = [r["action"] for r in res]
    assert actions.count("published") == 2
    assert actions[-1] == "deferred_cap"
    # watermark advanced to the 2nd publish, NOT past the deferred 3rd
    assert store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) == "2026-05-30T00:01:00Z"


def test_daily_cap_counts_against_prior_same_day_budget():
    store = InMemoryMeshStore(artifacts=[_artifact("a.py")])
    fixed_day = "2026-05-30"
    # already used 2 of a cap of 2 today -> nothing new publishes
    store.write("mesh_memory", {"agent_id": PUBLISHER_AGENT_ID,
                                "memory_type": "pr_publish_budget",
                                "content": json.dumps({"day": fixed_day, "count": 2})})

    class _Clk:
        def strftime(self, fmt):
            return fixed_day

    res = _pub(store, enabled=True, daily_cap=2, clock=lambda: _Clk()).run_once()
    assert res and res[0]["action"] == "deferred_cap"


# --- gitops backoff ---------------------------------------------------------

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


class _NoopForFiles(FakeGitOps):
    """FakeGitOps that reports a no-op (content already on base) for given files."""

    def __init__(self, noop_files, **kw):
        super().__init__(**kw)
        self._noop = set(noop_files)

    def publish(self, plan):
        if plan.file_path in self._noop:
            from zo_sentinel.publisher.gitops import PublishResult
            return PublishResult(ok=True, noop=True, branch=plan.branch,
                                 detail="no-op: artifact already on base (nothing to commit)")
        return super().publish(plan)


def test_noop_artifact_does_not_block_queue_or_burn_cap():
    """A no-op artifact (already on base) must dedup + advance the watermark so the
    newer artifact behind it still publishes -- and must NOT consume a daily-cap
    slot. Regression for the OPERATIONS.md head-of-line block."""
    store = InMemoryMeshStore(artifacts=[
        _artifact("OPERATIONS.md", built_at="2026-06-04T17:56:00Z", task="write_operations_doc"),
        _artifact("real.py", built_at="2026-06-04T18:00:00Z", task="build_real"),
    ])
    gitops = _NoopForFiles(["OPERATIONS.md"])
    res = _pub(store, enabled=True, gitops=gitops, daily_cap=1).run_once()
    actions = {r["file"]: r["action"] for r in res}
    assert actions["OPERATIONS.md"] == "noop"
    # the no-op did NOT eat the daily_cap=1 slot -> real.py still publishes
    assert actions["real.py"] == "published"
    assert len(gitops.published) == 1 and gitops.published[0].file_path == "real.py"
    # watermark advanced past BOTH (no head-of-line stall on the no-op)
    assert store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) == "2026-06-04T18:00:00Z"
    # both dedup keys recorded so neither is retried
    pub_rows = store.writes_of_type(PR_PUBLISHED_TYPE)
    recorded = json.loads(pub_rows[-1]["content"])
    assert any("OPERATIONS.md" in k for k in recorded)
    assert any("real.py" in k for k in recorded)


def test_watermark_persists_through_flaky_writes():
    """A transient write_service drop must not lose the watermark -- _write_durable
    retries, so the publisher keeps its place across the known :8772 instability."""
    class _FlakyStore(InMemoryMeshStore):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._wm_fails_left = 2          # first 2 watermark writes "time out"

        def write(self, table, row):
            if (row.get("memory_type") == WATERMARK_TYPE
                    and self._wm_fails_left > 0):
                self._wm_fails_left -= 1
                return False                  # simulate dropped write_service write
            return super().write(table, row)

    store = _FlakyStore(artifacts=[_artifact("a.py", built_at="2026-05-30T00:00:00Z")])
    _pub(store, enabled=True).run_once()
    # despite 2 dropped writes, the 3rd retry lands -> watermark persisted
    assert store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID) == "2026-05-30T00:00:00Z"
