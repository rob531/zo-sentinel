"""cadence_admin_api.py -- the CofC-ruled write path for the two deferred
cadence jobs: perspective snapshots (trust-diff baseline) + ask-corpus drift
guard. Replaces the never-built perspective_snapshot_daemon and
ask_corpus_drift_guard daemons.

COUNCIL RULING (FATHER 2026-07-08, docs/DECISION_CADENCE_WRITE_PATH_2026_07_08.md):
these are NOT daemons under the read-only decree. The decree governs the
factory DuckDB plane; a request-scoped, externally-triggered admin endpoint
writing to the prod PG plane the app already owns is an APPLICATION WRITE
PATH. The banned artifact is the long-lived in-process loop with unmanaged
lifecycle, not scheduled writes.

Binding MUSTs implemented here:
- bulk admin endpoints, externally triggered (tower scheduled task = primary)
- one job-status row per run (cadence_job_runs: started/finished/status/rows)
- enqueue-then-poll: POST returns a run_id immediately; work runs in a
  background task with its OWN session; GET /jobs/{run_id} polls
- advisory lock taken at job start (pg_try_advisory_lock on Postgres,
  process-local lock on sqlite), released in finally; fail closed if held
- cost ceilings (CADENCE_MAX_PERSPECTIVES, CADENCE_REINDEX_MAX_ROWS)
- missed-cadence surfaced at GET /api/admin/cadence/health (pipeline-watch
  reads this; overdue => alert)
- invocation key: X-Cadence-Key vs env CADENCE_ADMIN_KEY (Fly secret; tower
  copy lives in AgentVault) -- scoped to THESE endpoints only, constant-time
  compare, rotatable. Clerk admins also pass via the standard admin path.
"""
from __future__ import annotations

import hmac
import os
import threading
from datetime import datetime, timedelta
from typing import Optional

from fastapi import (APIRouter, BackgroundTasks, Depends, Header,
                     HTTPException)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (AskCorpusDoc, CadenceJobRun, McpServerRegistry,
                        Perspective)

router = APIRouter(prefix="/api/admin/cadence", tags=["cadence"])

JOB_SNAPSHOTS = "perspective_snapshots"
JOB_DRIFT = "ask_corpus_drift"

# Stable int64 keys for pg advisory locks (arbitrary, unique per job).
_LOCK_KEYS = {JOB_SNAPSHOTS: 84210701, JOB_DRIFT: 84210702}
# sqlite / dev fallback: process-local locks with the same semantics.
_LOCAL_LOCKS = {JOB_SNAPSHOTS: threading.Lock(), JOB_DRIFT: threading.Lock()}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# auth: scoped machine key OR Clerk admin. Fail closed.
# --------------------------------------------------------------------------
_bearer = HTTPBearer(auto_error=False)


def require_cadence_invoker(
        x_cadence_key: Optional[str] = Header(None),
        creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> str:
    """CONTRA safeguard 4: the machine key is scoped to cadence endpoints only,
    sourced from AgentVault tower-side / Fly secret app-side, rotatable, and
    compared constant-time. Anything else falls through to the same Clerk
    admin check every other admin surface uses."""
    configured = os.environ.get("CADENCE_ADMIN_KEY", "")
    if configured and x_cadence_key and hmac.compare_digest(configured, x_cadence_key):
        return "cadence-key"
    from verdict_breakdown_api import get_principal  # late: needs Clerk config
    principal = get_principal(creds)
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return principal.user_id


# --------------------------------------------------------------------------
# advisory lock (MUST 6): bounded hold = worker scope; released in finally.
# --------------------------------------------------------------------------
def _acquire(db: Session, job: str) -> bool:
    if db.get_bind().dialect.name == "postgresql":
        return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"),
                               {"k": _LOCK_KEYS[job]}).scalar())
    return _LOCAL_LOCKS[job].acquire(blocking=False)


def _release(db: Session, job: str) -> None:
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_unlock(:k)"),
                       {"k": _LOCK_KEYS[job]})
            db.commit()
        elif _LOCAL_LOCKS[job].locked():
            _LOCAL_LOCKS[job].release()
    except Exception:
        pass  # release is best-effort; pg session death frees the lock anyway


# --------------------------------------------------------------------------
# job-status rows (MUST 4)
# --------------------------------------------------------------------------
def _last_ok(db: Session, job: str) -> Optional[CadenceJobRun]:
    return db.execute(
        select(CadenceJobRun)
        .where(CadenceJobRun.job == job, CadenceJobRun.status == "ok")
        .order_by(CadenceJobRun.finished_at.desc())
        .limit(1)).scalars().first()


def _start_run(db: Session, job: str) -> CadenceJobRun:
    run = CadenceJobRun(job=job, status="running", started_at=datetime.utcnow())
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _finish(db: Session, run: CadenceJobRun, status: str,
            rows: Optional[int], detail: dict) -> None:
    run.status = status
    run.finished_at = datetime.utcnow()
    run.rows_affected = rows
    run.detail = detail
    db.commit()


def _record_noop(db: Session, job: str, detail: dict) -> CadenceJobRun:
    now = datetime.utcnow()
    run = CadenceJobRun(job=job, status="ok", started_at=now, finished_at=now,
                        rows_affected=0, detail=detail)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _serialize(run: CadenceJobRun) -> dict:
    return {"run_id": run.id, "job": run.job, "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "rows_affected": run.rows_affected, "detail": run.detail}


# --------------------------------------------------------------------------
# workers -- run in a background task with their OWN session (MUST 5)
# --------------------------------------------------------------------------
def _worker_session() -> Session:
    from app.db import SessionLocal  # late import so tests can rebind
    return SessionLocal()


def _run_snapshots(run_id: int) -> None:
    db = _worker_session()
    got_lock = False
    try:
        run = db.get(CadenceJobRun, run_id)
        got_lock = _acquire(db, JOB_SNAPSHOTS)
        if not got_lock:
            _finish(db, run, "failed", 0,
                    {"error": "advisory lock unavailable (another run in flight)"})
            return
        from perspective_diff_service import (diff_perspective,
                                              snapshot_perspective)
        cap = _env_int("CADENCE_MAX_PERSPECTIVES", 500)
        ids = [r for (r,) in db.execute(select(Perspective.id).limit(cap + 1)).all()]
        if len(ids) > cap:
            _finish(db, run, "failed", 0,
                    {"error": f"cost ceiling: >{cap} perspectives; raise "
                              "CADENCE_MAX_PERSPECTIVES deliberately"})
            return
        events = 0
        snapped = 0
        for pid in ids:
            # diff vs the previous baseline queues PerspectiveEvent rows
            # (the trust-diff product loop) ...
            d = diff_perspective(db, pid, queue_events=True)
            if not d.get("baseline"):
                events += (len(d.get("entered") or []) + len(d.get("left") or [])
                           + len(d.get("tier_changed") or []))
            # ... then re-baseline: exactly one snapshot per perspective/cycle.
            snapshot_perspective(db, pid)
            snapped += 1
        _finish(db, run, "ok", snapped,
                {"perspectives": snapped, "events_queued": events})
    except Exception as e:  # forced-fail gate G2: status=failed, lock released
        try:
            run = db.get(CadenceJobRun, run_id)
            if run is not None:
                _finish(db, run, "failed", None, {"error": str(e)[:500]})
        except Exception:
            pass
    finally:
        if got_lock:
            _release(db, JOB_SNAPSHOTS)
        db.close()


def _drift_stats(db: Session) -> dict:
    corpus = db.execute(select(func.count()).select_from(AskCorpusDoc)).scalar() or 0
    registry = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    max_indexed = db.execute(select(func.max(AskCorpusDoc.indexed_at))).scalar()
    max_assessed = db.execute(select(func.max(McpServerRegistry.last_assessed))).scalar()
    if registry <= 0:
        drift_pct = 0.0
    elif corpus == 0:
        drift_pct = 100.0
    else:
        drift_pct = abs(registry - corpus) * 100.0 / registry
    newer = bool(max_assessed and (max_indexed is None or max_assessed > max_indexed))
    return {"corpus_rows": corpus, "registry_rows": registry,
            "drift_pct": round(drift_pct, 2),
            "max_indexed_at": max_indexed.isoformat() if max_indexed else None,
            "max_last_assessed": max_assessed.isoformat() if max_assessed else None,
            "scores_newer_than_index": newer}


def _run_reindex(run_id: int) -> None:
    db = _worker_session()
    got_lock = False
    try:
        run = db.get(CadenceJobRun, run_id)
        got_lock = _acquire(db, JOB_DRIFT)
        if not got_lock:
            _finish(db, run, "failed", 0,
                    {"error": "advisory lock unavailable (another run in flight)"})
            return
        ceiling = _env_int("CADENCE_REINDEX_MAX_ROWS", 200_000)
        registry = db.execute(
            select(func.count()).select_from(McpServerRegistry)).scalar() or 0
        if registry == 0 or registry > ceiling:
            _finish(db, run, "failed", 0,
                    {"error": f"cost ceiling: registry_rows={registry} outside "
                              f"(0, {ceiling}]"})
            return
        from ask_corpus_indexer import reindex
        stats = reindex(db)
        corpus_after = db.execute(
            select(func.count()).select_from(AskCorpusDoc)).scalar() or 0
        _finish(db, run, "ok", corpus_after,
                {"reindex": stats, "corpus_rows_after": corpus_after})
    except Exception as e:
        try:
            run = db.get(CadenceJobRun, run_id)
            if run is not None:
                _finish(db, run, "failed", None, {"error": str(e)[:500]})
        except Exception:
            pass
    finally:
        if got_lock:
            _release(db, JOB_DRIFT)
        db.close()


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------
@router.post("/perspectives/run-snapshots")
def run_snapshots(background: BackgroundTasks, force: bool = False,
                  db: Session = Depends(get_session),
                  invoker: str = Depends(require_cadence_invoker)) -> dict:
    """Enqueue one snapshot+diff cycle over every saved perspective.
    Idempotent within CADENCE_MIN_INTERVAL_HOURS (default 12) unless force."""
    min_h = _env_int("CADENCE_MIN_INTERVAL_HOURS", 12)
    last = _last_ok(db, JOB_SNAPSHOTS)
    if (not force and last and last.finished_at and
            datetime.utcnow() - last.finished_at < timedelta(hours=min_h)):
        run = _record_noop(db, JOB_SNAPSHOTS,
                           {"skipped": True,
                            "reason": f"ok run within {min_h}h",
                            "last_ok": last.finished_at.isoformat()})
        return {"job": JOB_SNAPSHOTS, "run_id": run.id, "status": "ok",
                "skipped": True}
    run = _start_run(db, JOB_SNAPSHOTS)
    background.add_task(_run_snapshots, run.id)
    return {"job": JOB_SNAPSHOTS, "run_id": run.id, "status": "running",
            "poll": f"/api/admin/cadence/jobs/{run.id}"}


@router.post("/ask/drift-check")
def drift_check(background: BackgroundTasks, force: bool = False,
                db: Session = Depends(get_session),
                invoker: str = Depends(require_cadence_invoker)) -> dict:
    """Cheap drift measurement inline; the ~66k-row reindex only ever runs as
    an enqueued background job (MUST 5). No drift => records an ok no-op run
    (a clean check IS a successful cadence)."""
    stats = _drift_stats(db)
    pct = _env_float("CADENCE_DRIFT_PCT", 5.0)
    triggered = bool(force or stats["drift_pct"] > pct
                     or stats["scores_newer_than_index"])
    if not triggered:
        run = _record_noop(db, JOB_DRIFT, {"triggered": False, **stats})
        return {"job": JOB_DRIFT, "run_id": run.id, "status": "ok",
                "triggered": False, "drift": stats}
    run = _start_run(db, JOB_DRIFT)
    background.add_task(_run_reindex, run.id)
    return {"job": JOB_DRIFT, "run_id": run.id, "status": "running",
            "triggered": True, "drift": stats,
            "poll": f"/api/admin/cadence/jobs/{run.id}"}


@router.get("/jobs/{run_id}")
def get_job(run_id: int, db: Session = Depends(get_session),
            invoker: str = Depends(require_cadence_invoker)) -> dict:
    run = db.get(CadenceJobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such run")
    return _serialize(run)


@router.get("/health")
def cadence_health(db: Session = Depends(get_session),
                   invoker: str = Depends(require_cadence_invoker)) -> dict:
    """MUST 7: the missed-cadence surface. overdue=true (=> alert) when a job
    has no successful run within CADENCE_SLA_HOURS (default 36 = daily cadence
    + slack). Never-ran counts as overdue -- honest fail-closed, same doctrine
    as freshness UNKNOWN."""
    sla_h = _env_int("CADENCE_SLA_HOURS", 36)
    now = datetime.utcnow()
    jobs = {}
    for job in (JOB_SNAPSHOTS, JOB_DRIFT):
        last = _last_ok(db, job)
        overdue = (last is None or last.finished_at is None
                   or (now - last.finished_at) > timedelta(hours=sla_h))
        jobs[job] = {"last_ok": (last.finished_at.isoformat()
                                 if last and last.finished_at else None),
                     "overdue": overdue}
    return {"sla_hours": sla_h, "jobs": jobs,
            "alert": any(j["overdue"] for j in jobs.values())}


# --------------------------------------------------------------------------
# acceptance self-test (sqlite, no network) -- prints PASS
# --------------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["DATABASE_URL"] = "sqlite://"
    os.environ["CADENCE_ADMIN_KEY"] = "test-key"
    os.environ["CADENCE_MIN_INTERVAL_HOURS"] = "12"

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.db as app_db
    from app.db import Base
    from app.models import McpLlmAxisScore

    eng = create_engine("sqlite://",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng)
    app_db.SessionLocal = TestSession  # workers pick this up (late import)

    test_app = FastAPI()
    test_app.include_router(router)

    def _override():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    test_app.dependency_overrides[get_session] = _override
    client = TestClient(test_app)
    H = {"X-Cadence-Key": "test-key"}

    seed = TestSession()
    now = datetime.utcnow()
    seed.add_all([
        McpServerRegistry(server_id="s1", name="Alpha MCP", verdict="LOW",
                          risk_tier="LOW", registry_source="github",
                          description="alpha test server",
                          last_assessed=now),
        McpServerRegistry(server_id="s2", name="Beta MCP", verdict="HIGH",
                          risk_tier="HIGH", registry_source="npm",
                          description="beta test server",
                          last_assessed=now),
        McpLlmAxisScore(id=1, server_id="s1", axis_name="overall_risk",
                        label="LOW", model_version="v3.0", scored_at=now),
        McpLlmAxisScore(id=2, server_id="s2", axis_name="overall_risk",
                        label="HIGH", model_version="v3.0", scored_at=now),
        Perspective(id="p1", org_id="o1", name="All", facet_filters={},
                    created_by="selftest"),
        Perspective(id="p2", org_id="o1", name="High only",
                    facet_filters={"risk_tier": ["HIGH"]},
                    created_by="selftest"),
    ])
    seed.commit()
    seed.close()

    # auth fails closed
    assert client.post("/api/admin/cadence/perspectives/run-snapshots").status_code in (401, 403, 503)

    # 1) snapshot cycle: every perspective exactly once
    r = client.post("/api/admin/cadence/perspectives/run-snapshots", headers=H)
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    j = client.get(f"/api/admin/cadence/jobs/{rid}", headers=H).json()
    assert j["status"] == "ok", j
    assert j["rows_affected"] == 2, j
    s = TestSession()
    from app.models import PerspectiveSnapshot
    n_snaps = s.execute(select(func.count()).select_from(PerspectiveSnapshot)).scalar()
    assert n_snaps == 2, f"expected 2 snapshots, got {n_snaps}"
    s.close()

    # 2) idempotent within the interval
    r2 = client.post("/api/admin/cadence/perspectives/run-snapshots", headers=H).json()
    assert r2.get("skipped") is True, r2

    # 3) drift guard: empty corpus vs 2 registry rows => triggered reindex
    r3 = client.post("/api/admin/cadence/ask/drift-check", headers=H)
    assert r3.status_code == 200, r3.text
    body = r3.json()
    assert body["triggered"] is True, body
    j3 = client.get(f"/api/admin/cadence/jobs/{body['run_id']}", headers=H).json()
    assert j3["status"] == "ok", j3
    assert (j3["rows_affected"] or 0) > 0, j3

    # 4) second drift check: corpus now in sync => ok no-op
    r4 = client.post("/api/admin/cadence/ask/drift-check", headers=H).json()
    assert r4["triggered"] is False, r4

    # 5) health: both jobs green, no alert
    h = client.get("/api/admin/cadence/health", headers=H).json()
    assert h["alert"] is False, h
    assert all(v["last_ok"] for v in h["jobs"].values()), h

    print("PASS")
