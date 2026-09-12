"""
test_pr_publisher.py -- hermetic tests for the goose -> GitHub-PR bridge.

Uses the ingestor's InMemoryMeshStore + a FakeGitOps + an injected content
resolver, so no network, no git, no host. Proves: dormant by default (writes
nothing, plans only), publishes when enabled, dedups across runs, blocks unsafe
artifacts before they ever reach a PR, and carries goose ladder-tier provenance.
"""
from __future__ import annotations

import json
import os
import tempfile
import random
import types
from datetime import datetime, timezone

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
    # HERMETIC: the publisher parks a directive when it refuses a hollow build,
    # which writes sentinels under home/ and the durable quarantine store. Without
    # a sandbox home those writes land in the REAL /home/workspace/zo_sentinel --
    # a test run would park a live directive. Give every test its own throwaway.
    _sandbox = tempfile.mkdtemp(prefix="pub-test-")
    kw.setdefault("home", _sandbox)
    kw.setdefault("quarantine_dir", os.path.join(_sandbox, "quarantine"))
    os.makedirs(os.path.join(_sandbox, "directives"), exist_ok=True)
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


# --- same-module duplicate guard (2026-07-10: PRs #1397/#1398 were the same
# file from two directives -> both merged; dedup_key = file|built_at so the
# key-level dedup can't see it) -------------------------------------------

def test_duplicate_module_same_pass_skipped():
    # Two DIFFERENT directives, same target file, an hour apart.
    store = InMemoryMeshStore(artifacts=[
        _artifact("dup.py", built_at="2026-07-10T10:00:00Z", task="build_dup_a"),
        _artifact2("dup.py", built_at="2026-07-10T11:00:00Z", task="build_dup_b"),
    ])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops).run_once()
    assert [r["action"] for r in res] == ["published", "duplicate_module"]
    assert len(gitops.published) == 1
    # the duplicate's key is recorded so it is never re-attempted
    pub_rows = store.writes_of_type(PR_PUBLISHED_TYPE)
    assert "dup.py|2026-07-10T11:00:00Z" in json.loads(pub_rows[-1]["content"])


def _at(iso):
    """A pinned clock. run_once() prunes `published_files` against WALL-CLOCK now
    (retention = 10x the 3-day guard window = 30 days), while the guard itself
    compares two ARTIFACT timestamps. With absolute fixture dates and a real
    clock these two cross-run tests age out of their own state: on
    2026-08-09T10:00:00Z -- exactly 30 days after the 2026-07-10T10:00:00Z
    fixture -- the prune started emptying published_files, the guard saw nothing
    to match, and the assertion flipped to 'published'. Pin the clock so the
    guard, not the calendar, decides the outcome."""
    return lambda: datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_duplicate_module_across_runs_skipped(tmp_path):
    state = str(tmp_path / "pub_state.json")
    clock = _at("2026-07-11T12:00:00Z")
    store = InMemoryMeshStore(artifacts=[
        _artifact("dup.py", built_at="2026-07-10T10:00:00Z", task="build_dup_a"),
    ])
    _pub(store, enabled=True, state_file=state, clock=clock).run_once()
    store2 = InMemoryMeshStore(artifacts=[
        _artifact2("dup.py", built_at="2026-07-11T10:00:00Z", task="build_dup_b"),
    ])
    gitops2 = FakeGitOps()
    res = _pub(store2, enabled=True, gitops=gitops2, state_file=state,
               clock=clock).run_once()
    assert res[0]["action"] == "duplicate_module"
    assert gitops2.published == []


def test_same_file_outside_window_publishes(tmp_path):
    state = str(tmp_path / "pub_state.json")
    clock = _at("2026-06-20T12:00:00Z")
    store = InMemoryMeshStore(artifacts=[
        _artifact("old.py", built_at="2026-05-30T00:00:00Z", task="build_old"),
    ])
    _pub(store, enabled=True, state_file=state, clock=clock).run_once()
    store2 = InMemoryMeshStore(artifacts=[
        _artifact2("old.py", built_at="2026-06-20T00:00:00Z", task="build_old_v2"),
    ])
    gitops2 = FakeGitOps()
    res = _pub(store2, enabled=True, gitops=gitops2, state_file=state,
               clock=clock).run_once()
    # published because 21 days > the 3-day guard window -- NOT because the
    # state was pruned away (which is what made this test green for free).
    assert res[0]["action"] == "published"
    assert len(gitops2.published) == 1


def _artifact2(file, built_at, task):
    """Same as _artifact but with a distinct row id per (file, built_at)."""
    c = {"file": file, "built_at": built_at, "phase": "p1", "bytes": 10,
         "interface": "compute_score", "task": task}
    return (f"row-{file}-{built_at}", json.dumps(c))

# --- anti-hollow pre-publish gate (mirrors tests/ci/no_hollow_scaffold.py) ---

def test_hollow_fastapi_scaffold_blocked_before_pr():
    # The #1438 failure mode: an ingestor wrapped in a standalone FastAPI app
    # with no real data layer. Must never reach a PR.
    src = "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/x')\ndef x():\n    return {}\n"
    store = InMemoryMeshStore(artifacts=[_artifact("hollow_api.py")])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops, content=src).run_once()
    assert res[0]["action"] == "hollow_blocked"
    assert "no real data layer" in res[0]["detail"]
    assert gitops.published == []


def test_hollow_mock_text_blocked_before_pr():
    # The #1449 failure mode: inline "# Mock ..." test scaffolding in a root
    # module trips the CI mock regex; block it here instead of burning a PR.
    src = "import requests_mock\n# Mock write_service responses\nprint('x')\n"
    store = InMemoryMeshStore(artifacts=[_artifact("mocky.py")])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops, content=src).run_once()
    assert res[0]["action"] == "hollow_blocked"
    assert gitops.published == []


def test_real_datalayer_api_passes_hollow_gate():
    src = ("from fastapi import APIRouter\nfrom app.db import get_session\n"
           "router = APIRouter()\n@router.get('/y')\ndef y():\n    return 1\n")
    store = InMemoryMeshStore(artifacts=[_artifact("real_api.py")])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops, content=src).run_once()
    assert res[0]["action"] == "published"
    assert len(gitops.published) == 1


def test_non_root_and_non_py_skip_hollow_gate():
    # CI's no-hollow gate only inspects added ROOT-LEVEL .py modules; the
    # pre-publish scan must stay exactly as permissive.
    src = "app = FastAPI()\n"
    store = InMemoryMeshStore(artifacts=[_artifact("app/sub_module.py"),
                                         _artifact("notes.md", built_at="2026-05-30T00:00:01Z")])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops, content=src).run_once()
    assert [r["action"] for r in res] == ["published", "published"]


def test_hollow_block_advances_watermark_and_does_not_stall_queue():
    src_bad = "app = FastAPI()\n"
    store = InMemoryMeshStore(artifacts=[_artifact("hollow_api.py"),
                                         _artifact("good.py", built_at="2026-05-30T00:00:01Z")])
    gitops = FakeGitOps()
    pub = _pub(store, enabled=True, gitops=gitops,
               content=None)
    # per-artifact content: hollow for the first file, clean for the second
    pub._resolver = lambda art: src_bad if art.file == "hollow_api.py" else "print('ok')\n"
    res = pub.run_once()
    actions = {r["file"]: r["action"] for r in res}
    assert actions["hollow_api.py"] == "hollow_blocked"
    assert actions["good.py"] == "published"


# --- saturated-family gate (council enforcement in code, 2026-07-12) ---------

def test_saturated_family_skipped():
    store = InMemoryMeshStore(artifacts=[
        _artifact("fleet_exploit_surface_api.py", task="build_fleet_exploit"),
        _artifact("nvd_cve2_feed_loader.py", task="build_nvd_loader"),
    ])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops).run_once()
    by_file = {r["file"]: r["action"] for r in res}
    assert by_file["fleet_exploit_surface_api.py"] == "saturated_family"
    assert by_file["nvd_cve2_feed_loader.py"] == "published"
    assert len(gitops.published) == 1
    # saturated artifact's key is recorded so it never re-surfaces
    pub_rows = store.writes_of_type(PR_PUBLISHED_TYPE)
    assert "fleet_exploit_surface_api.py|2026-05-30T00:00:00Z" in json.loads(
        pub_rows[-1]["content"])


def test_saturation_gate_env_off(monkeypatch):
    monkeypatch.setenv("PR_SATURATION_GATE", "0")
    store = InMemoryMeshStore(artifacts=[
        _artifact("fleet_exploit_surface_api.py", task="build_fleet_exploit"),
    ])
    gitops = FakeGitOps()
    res = _pub(store, enabled=True, gitops=gitops).run_once()
    assert res[0]["action"] == "published"


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
