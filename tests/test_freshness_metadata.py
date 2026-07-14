"""CI gate for the P1 freshness surface (docs/DESIGN_NEXT_BUILD_TARGETS_2026_07.md).

Runs the module's real __main__ self-test as a subprocess (sqlite, no network)
plus contract tests: response shape, FRESH/STALE boundary vs the declared SLA,
honest UNKNOWN for never-scored servers, and the fail-closed gate helper that
keyed/badge surfaces (scorecard_badge_api) must consume before mounting.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
REPO = pathlib.Path(__file__).resolve().parents[1]


def test_selftest_passes():
    env = {**os.environ, "DATABASE_URL": "sqlite://", "CLERK_PUBLISHABLE_KEY": ""}
    proc = subprocess.run([sys.executable, str(REPO / "freshness_metadata_api.py")],
                          capture_output=True, text=True, timeout=120,
                          env=env, cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0 and "PASS" in out, out[-2000:]


def _session():
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_shape_contract_and_sla_boundary(monkeypatch):
    monkeypatch.setenv("FRESHNESS_SLA_DAYS", "30")
    from app.models import McpLlmAxisScore
    from freshness_metadata_api import server_freshness, is_fresh
    s = _session()
    now = datetime.utcnow()
    s.add_all([
        McpLlmAxisScore(id=1, server_id="edge", axis_name="overall_risk",
                        model_version="v3.0",
                        scored_at=now - timedelta(days=29, hours=23)),
        McpLlmAxisScore(id=2, server_id="past", axis_name="overall_risk",
                        model_version="v3.0",
                        scored_at=now - timedelta(days=30, hours=1)),
    ])
    s.commit()
    f = server_freshness(s, "edge")
    assert set(f) == {"server_id", "last_scored_at", "model_version",
                      "sla_days", "sla_status"}          # exact KL-doc shape
    assert f["sla_status"] == "FRESH"
    assert server_freshness(s, "past")["sla_status"] == "STALE"
    # fail-closed gate: STALE and UNKNOWN are both not-fresh
    assert is_fresh(s, "edge") and not is_fresh(s, "past")
    assert server_freshness(s, "ghost")["sla_status"] == "UNKNOWN"
    assert not is_fresh(s, "ghost")


def test_bad_sla_env_falls_back(monkeypatch):
    # Falls back to the ONE shared default (freshness_gate.DEFAULT_SLA_DAYS = 7).
    # Was 30 until 2026-07-14, which is why 11-day-old scores read "FRESH".
    monkeypatch.setenv("FRESHNESS_SLA_DAYS", "not-a-number")
    from freshness_metadata_api import sla_days
    assert sla_days() == 7

# --- public observability surfaces (chairman-built 2026-07-14) ---------------

def test_scoring_freshness_surface_module_shape():
    import scoring_freshness_surface as m
    assert hasattr(m, "router")
    paths = [r.path for r in m.router.routes]
    assert "/freshness" in paths


def test_runtime_deploy_info_endpoint_module_shape():
    import runtime_deploy_info_endpoint as m
    assert hasattr(m, "router")
    paths = [r.path for r in m.router.routes]
    assert "/version" in paths
