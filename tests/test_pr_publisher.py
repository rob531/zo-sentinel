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
from zo_sentinel.publisher.gitops import CliGitOps, FakeGitOps, _is_rate_limited
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


def test_gitops_no_retry_on_ordinary_failure():
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return types.SimpleNamespace(returncode=1, stderr="fatal: some other error", stdout="")

    g = CliGitOps("/tmp/nope", sleep=lambda s: None)
    r = g._run_with_backoff(fake)
    assert r.returncode == 1 and calls["n"] == 1   # no retry on non-rate-limit
