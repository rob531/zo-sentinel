"""cadence_job_runs_api.py -- read-only view of cadence daemon job run history.

Mounted by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class JobRun(BaseModel):
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None
    detail: Optional[dict] = None


class JobsSummaryEntry(BaseModel):
    job: str
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    total_runs: int = 0


def _job_run_dict(r: CadenceJobRun) -> dict:
    return {
        "job": r.job,
        "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "rows_affected": r.rows_affected,
        "detail": r.detail,
    }


@router.get("/job-runs")
def list_job_runs(
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_session),
) -> dict:
    """Return all cadence job run records, newest first."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    total = db.execute(select(func.count()).select_from(CadenceJobRun)).scalar() or 0
    rows = (
        db.execute(
            select(CadenceJobRun)
            .order_by(CadenceJobRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {
        "runs": [_job_run_dict(r) for r in rows],
        "count": len(rows),
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/job-runs/{job}", response_model=JobRun)
def get_latest_job_run(
    job: str,
    db: Session = Depends(get_session),
) -> JobRun:
    """Return the most recent run for a named job."""
    row = (
        db.execute(
            select(CadenceJobRun)
            .where(CadenceJobRun.job == job)
            .order_by(CadenceJobRun.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No runs found for job {job!r}")
    return JobRun(**_job_run_dict(row))


@router.get("/jobs-summary")
def jobs_summary(
    db: Session = Depends(get_session),
) -> dict:
    """Return one row per distinct job with the last-run timestamp, status, and total run count."""
    sub = (
        select(
            CadenceJobRun.job,
            func.max(CadenceJobRun.started_at).label("last_run_at"),
        )
        .group_by(CadenceJobRun.job)
        .subquery()
    )
    rows = (
        db.execute(
            select(
                CadenceJobRun.job,
                sub.c.last_run_at,
                CadenceJobRun.status.label("last_status"),
                func.count().label("total_runs"),
            )
            .join(sub, CadenceJobRun.job == sub.c.job)
            .where(CadenceJobRun.started_at == sub.c.last_run_at)
            .group_by(CadenceJobRun.job, CadenceJobRun.status, sub.c.last_run_at)
            .order_by(sub.c.last_run_at.desc())
        )
        .all()
    )
    entries = [
        {
            "job": row[0],
            "last_run_at": row[1].isoformat() if row[1] else None,
            "last_status": row[2],
            "total_runs": row[3],
        }
        for row in rows
    ]
    return {"jobs": entries, "count": len(entries)}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    now = datetime(2026, 7, 10, 12, 0, 0)
    later = datetime(2026, 7, 10, 13, 0, 0)
    s.add(
        CadenceJobRun(
            id=1,
            job="scorer",
            status="ok",
            started_at=now,
            finished_at=later,
            rows_affected=42,
            detail={"cycle": 1},
        )
    )
    s.add(
        CadenceJobRun(
            id=2,
            job="heartbeat",
            status="failed",
            started_at=now,
            finished_at=later,
            rows_affected=None,
            detail={"error": "timeout"},
        )
    )
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # /job-runs returns list
    r = c.get("/api/cadence/job-runs")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] >= 2, j
    assert len(j["runs"]) >= 2, j

    # /job-runs/{job} returns latest for named job
    r = c.get("/api/cadence/job-runs/scorer")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["job"] == "scorer", j
    assert j["status"] == "ok", j

    # /job-runs/{job} 404 on unknown
    r = c.get("/api/cadence/job-runs/unknown_job")
    assert r.status_code == 404, r.text

    # /jobs-summary returns distinct jobs
    r = c.get("/api/cadence/jobs-summary")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["count"] >= 2, j
    job_names = {e["job"] for e in j["jobs"]}
    assert "scorer" in job_names, j

    print("PASS")
