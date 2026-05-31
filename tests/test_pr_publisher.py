"""
test_pr_publisher.py -- hermetic tests for the goose -> GitHub-PR bridge.

Uses the ingestor's InMemoryMeshStore + a FakeGitOps + an injected content
resolver, so no network, no git, no host. Proves: dormant by default (writes
nothing, plans only), publishes when enabled, dedups across runs, blocks unsafe
artifacts before they ever reach a PR, and carries goose ladder-tier provenance.
"""
from __future__ import annotations

import json

from zo_sentinel.ingestor.store import InMemoryMeshStore
from zo_sentinel.publisher.gitops import FakeGitOps
from zo_sentinel.publisher.publisher import (
    PR_PUBLISHED_TYPE,
    PUBLISHER_AGENT_ID,
    Publisher,
)


def _artifact(file, built_at="2026-05-30T00:00:00Z", task="build_x", tier=None):
    c = {"file": file, "built_at": built_at, "phase": "p1", "bytes": 10,
         "interface": "compute_score", "task": task}
    if tier:
        c["tier"] = tier
    return (f"row-{file}", json.dumps(c))


def _pub(store, enabled, content="print('ok')\n", gitops=None):
    return Publisher(
        store,
        gitops=gitops or FakeGitOps(),
        content_resolver=lambda art: content,
        enabled_override=enabled,
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
    pub = Publisher(store, gitops=FakeGitOps(),
                    content_resolver=lambda art: None, enabled_override=True)
    res = pub.run_once()
    assert res[0]["action"] == "skip"
