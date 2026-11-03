"""logic.py -- cadence scan cost computation over the real data layer."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, McpServerRegistry


_FAILURE_STATUSES = {"failed", "failure", "error"}


def registry_counts(db: Session) -> Dict[str, int]:
    """Count registered servers grouped by registry source."""
    rows = db.execute(
        select(McpServerRegistry.registry_source, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.registry_source)
    ).all()
    return {source or "unknown": int(count) for source, count in rows}


def compute_cost_ledger(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compute per-job cadence activity over the requested number of days."""
    window_days = max(1, int(days))
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    runs = db.execute(
        select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    ).scalars().all()

    by_job: Dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        by_job.setdefault(run.job, []).append(run)

    jobs: List[Dict[str, Any]] = []
    for job in sorted(by_job):
        job_runs = by_job[job]
        durations: List[float] = []

        for run in job_runs:
            if run.started_at is not None and run.finished_at is not None:
                seconds = (run.finished_at - run.started_at).total_seconds()
                durations.append(max(0.0, float(seconds)))

        failures = sum(
            1
            for run in job_runs
            if (run.status or "").strip().lower() in _FAILURE_STATUSES
        )
        rows_affected = sum(int(run.rows_affected or 0) for run in job_runs)

        jobs.append(
            {
                "job": job,
                "runs": len(job_runs),
                "failures": failures,
                "median_seconds": float(median(durations)) if durations else 0.0,
                "rows_affected": rows_affected,
            }
        )

    return {"days": window_days, "jobs": jobs}


def cost_ledger(db: Session, days: int = 30) -> Dict[str, Any]:
    return compute_cost_ledger(db, days)


def get_cost_ledger(db: Session, days: int = 30) -> Dict[str, Any]:
    return compute_cost_ledger(db, days)


def build_cost_ledger(db: Session, days: int = 30) -> Dict[str, Any]:
    return compute_cost_ledger(db, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with TestSession() as db:
        db.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=30),
                    rows_affected=4,
                    detail="failure",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1) + timedelta(seconds=10),
                    rows_affected=2,
                    detail="ok",
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3) + timedelta(seconds=60),
                    rows_affected=6,
                    detail="ok",
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/cadence/cost")
    def cadence_cost(days: int = 30, db: Session = Depends(get_session)):
        return compute_cost_ledger(db, days)

    test_app.dependency_overrides[get_session] = lambda: TestSession()
    client = TestClient(test_app)
    response = client.get("/api/cadence/cost", params={"days": 30})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2
    failing_job = next(job for job in payload["jobs"] if job["job"] == "alpha")
    assert failing_job["failures"] == 1
    assert failing_job["median_seconds"] >= 0
    print("PASS")