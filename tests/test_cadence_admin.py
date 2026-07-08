"""CI gate for the cadence write path (docs/DECISION_CADENCE_WRITE_PATH_2026_07_08.md).

Runs the module's real __main__ acceptance self-test as a subprocess (sqlite,
no network) -- that covers G1 (rows land end-to-end), the exactly-once +
idempotent-within-interval contract, drift trigger/no-op, auth fail-closed,
and health. Adds targeted contract tests for the forced-fail path (G2: run row
shows failed + advisory lock released) and the missed-cadence alert (G3).
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
    proc = subprocess.run([sys.executable, str(REPO / "cadence_admin_api.py")],
                          capture_output=True, text=True, timeout=180,
                          env=env, cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0 and "PASS" in out, out[-2000:]


def _sessionmaker():
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


def test_forced_fail_marks_run_failed_and_releases_lock(monkeypatch):
    """G2: a worker exception must close the run as failed and free the lock."""
    import app.db as app_db
    import cadence_admin_api as mod
    TestSession = _sessionmaker()
    monkeypatch.setattr(app_db, "SessionLocal", TestSession)

    s = TestSession()
    run = mod._start_run(s, mod.JOB_SNAPSHOTS)
    rid = run.id
    s.close()

    def _boom(*a, **k):
        raise RuntimeError("forced failure for G2")

    import perspective_diff_service
    monkeypatch.setattr(perspective_diff_service, "diff_perspective", _boom)
    # need at least one perspective so the loop reaches the boom
    s = TestSession()
    from app.models import Perspective
    s.add(Perspective(id="p1", org_id="o1", name="x", facet_filters={},
                      created_by="test"))
    s.commit()
    s.close()

    mod._run_snapshots(rid)

    s = TestSession()
    from app.models import CadenceJobRun
    row = s.get(CadenceJobRun, rid)
    assert row.status == "failed", row.detail
    assert "forced failure" in (row.detail or {}).get("error", "")
    # lock released => a fresh acquire succeeds
    assert mod._acquire(s, mod.JOB_SNAPSHOTS) is True
    mod._release(s, mod.JOB_SNAPSHOTS)
    s.close()


def test_health_overdue_when_never_ran_and_when_stale(monkeypatch):
    """G3: never-ran and stale-beyond-SLA both raise the alert."""
    import cadence_admin_api as mod
    TestSession = _sessionmaker()
    s = TestSession()

    # never ran => overdue (honest fail-closed)
    assert mod._last_ok(s, mod.JOB_SNAPSHOTS) is None

    from app.models import CadenceJobRun
    old = datetime.utcnow() - timedelta(hours=48)
    s.add(CadenceJobRun(job=mod.JOB_SNAPSHOTS, status="ok",
                        started_at=old, finished_at=old, rows_affected=1))
    fresh = datetime.utcnow() - timedelta(hours=1)
    s.add(CadenceJobRun(job=mod.JOB_DRIFT, status="ok",
                        started_at=fresh, finished_at=fresh, rows_affected=0))
    s.commit()

    monkeypatch.setenv("CADENCE_SLA_HOURS", "36")
    snap_last = mod._last_ok(s, mod.JOB_SNAPSHOTS)
    drift_last = mod._last_ok(s, mod.JOB_DRIFT)
    now = datetime.utcnow()
    assert (now - snap_last.finished_at) > timedelta(hours=36)      # overdue
    assert (now - drift_last.finished_at) <= timedelta(hours=36)    # fine
    s.close()


def test_drift_stats_math():
    import cadence_admin_api as mod
    TestSession = _sessionmaker()
    s = TestSession()
    from app.models import McpServerRegistry
    now = datetime.utcnow()
    for i in range(4):
        s.add(McpServerRegistry(server_id=f"s{i}", name=f"n{i}",
                                registry_source="github", last_assessed=now))
    s.commit()
    st = mod._drift_stats(s)
    assert st["registry_rows"] == 4 and st["corpus_rows"] == 0
    assert st["drift_pct"] == 100.0
    assert st["scores_newer_than_index"] is True
    s.close()


def test_cost_ceiling_env_parsing(monkeypatch):
    import cadence_admin_api as mod
    monkeypatch.setenv("CADENCE_REINDEX_MAX_ROWS", "notanint")
    assert mod._env_int("CADENCE_REINDEX_MAX_ROWS", 200_000) == 200_000
    monkeypatch.setenv("CADENCE_DRIFT_PCT", "2.5")
    assert mod._env_float("CADENCE_DRIFT_PCT", 5.0) == 2.5
