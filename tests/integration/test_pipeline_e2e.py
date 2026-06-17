"""Full-pipeline E2E: directive -> promoter -> build_artifact -> publisher.

Unlike test_db_roundtrip.py (which exercises the write_service DB *contract* in
isolation), this wires the REAL pipeline MODULES together end to end and runs
them against the real-DuckDB write_service:

    1. ARCHITECT (stubbed)  -- seed a valid directive into proposed/
    2. PROMOTER  (real)     -- proposed_to_pending_promoter.run_once: proposed -> pending
    3. BUILD     (stubbed)  -- the real build_routing.build_artifact_row shapes the
                               mesh_memory row a live goose build emits; written via
                               the real HttpMeshStore -> real write_service -> DuckDB
    4. PUBLISHER (real)     -- Publisher(store=HttpMeshStore, gitops=FakeGitOps)
                               reads that artifact back over HTTP and opens a (fake) PR

The two LLM-bearing rungs (architect, goose build) are stubbed -- a CI runner has
no LLM/ladder -- but every plumbing module between them is the real thing, run
against a real DB. This is the integration the per-PR smoke-ladder can't give:
it catches breaks in how the stages HAND OFF (the #174 "write returned ok but the
publisher's read_build_artifacts_since never saw it" class of bug).

Runs nightly (.github/workflows/e2e-nightly.yml); needs the it_write_service up.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from zo_sentinel.build_routing import build_artifact_row  # noqa: E402
from zo_sentinel.ingestor.store import (  # noqa: E402
    BUILD_ARTIFACT_TYPE,
    BUILDER_AGENT_ID,
    HttpMeshStore,
)
from zo_sentinel.promoters import proposed_to_pending_promoter as promoter  # noqa: E402
from zo_sentinel.publisher.gitops import FakeGitOps  # noqa: E402
from zo_sentinel.publisher.publisher import Publisher  # noqa: E402

WS = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")
T = 15

# mesh_memory is the builder/mesh table -- NOT one of the 17 app tables in
# schemas/duckdb_schema.json, so the it_write_service doesn't auto-create it.
# Create it here (the columns HttpMeshStore reads/writes) via /execute.
MESH_DDL = (
    "CREATE TABLE IF NOT EXISTS mesh_memory ("
    "id BIGINT, agent_id VARCHAR, memory_type VARCHAR, "
    "content VARCHAR, created_at VARCHAR, importance DOUBLE)"
)

VALID_DESCRIPTION = (
    "A sufficiently long directive description that clears the 50-char minimum "
    "validation rule the directive pipeline enforces on proposals."
)


def _seed_mesh_table():
    r = requests.post(f"{WS}/execute", json={"sql": MESH_DDL}, timeout=T)
    r.raise_for_status()
    assert r.json().get("ok") is True


def test_full_pipeline_directive_to_pr(tmp_path: Path):
    assert requests.get(f"{WS}/health", timeout=T).status_code == 200, "write_service must be up"
    _seed_mesh_table()
    store = HttpMeshStore(base_url=WS)

    # --- Stage 1+2: architect-output -> PROMOTER (real) -> pending ----------
    proposed = tmp_path / "proposed"
    pending = tmp_path / "pending"
    proposed.mkdir()
    pending.mkdir()
    output_file = f"zo_sentinel/e2e_probe_{int(time.time())}.py"
    directive = {
        "task": "e2e_pipeline_probe",
        "handler": "generate_file",
        "output_file": output_file,
        "complexity": "medium",
        "description": VALID_DESCRIPTION,
        "source": "directive_architect",
        "proposed_at": "2026-06-16T00:00:00Z",
    }
    prop = proposed / "e2e_probe.json"
    prop.write_text(json.dumps(directive), encoding="utf-8")
    old = time.time() - 120  # clear min_age
    os.utime(prop, (old, old))

    counters = promoter.run_once(
        proposed_dir=proposed, pending_dir=pending,
        min_age_secs=0, max_per_cycle=10, dry_run=False,
        directives_root=tmp_path,
    )
    # the proposal left proposed/ and a pending directive now exists
    assert not prop.exists(), f"promoter should have consumed the proposal; counters={counters}"
    assert list(pending.glob("*.json")), f"promoter produced no pending directive; counters={counters}"

    # --- Stage 3: BUILD (stubbed shape, real row) -> real DB ----------------
    # build_artifact_row already returns the FULL mesh_memory row
    # ({id, agent_id, memory_type, content(JSON), importance}); we only add the
    # created_at column the publisher's watermarked read orders/filters on.
    artifact = build_artifact_row(
        file=output_file, content_bytes=128, context_type="module",
        tier="zo-ladder-medium", model="minimax", backend="goose",
        phase="build", task="e2e_pipeline_probe",
        built_at="2026-06-16T00:01:00Z",
    )
    assert artifact["agent_id"] == BUILDER_AGENT_ID
    assert artifact["memory_type"] == BUILD_ARTIFACT_TYPE
    artifact["created_at"] = "2026-06-16T00:01:30Z"
    ok = store.write("mesh_memory", artifact)
    assert ok, "build_artifact write to write_service failed"

    # the #174 lesson: 'ok' is not enough -- it must actually land + be readable
    # the exact way the publisher reads it
    seen = store.read_build_artifacts_since(None, 50)
    files = []
    for _id, content, _created in seen:
        c = json.loads(content) if isinstance(content, str) else content
        files.append(c.get("file"))
    assert output_file in files, f"publisher's artifact read didn't see the build; saw {files}"

    # --- Stage 4: PUBLISHER (real) + FakeGitOps -> (fake) PR ----------------
    pub = Publisher(
        store=store,
        gitops=FakeGitOps("https://github.com/rob531/zo-sentinel"),
        home=str(tmp_path),
        enabled_override=True,
        content_resolver=lambda art: "# e2e stub built file\nprint('ok')\n",
        daily_cap=100,
        pr_spacing_sec=0.0,
        sleep=lambda _s: None,
    )
    published = pub.run_once(limit=10)
    assert published, "publisher consumed the build_artifact and opened a (fake) PR"
    pub_files = " ".join(json.dumps(p) for p in published)
    assert output_file in pub_files or "e2e_pipeline_probe" in pub_files, (
        f"published PR didn't reference the built artifact: {published}"
    )
