"""Logic layer for cadence job run failure summaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CadenceJobRun


def get_failures(db: Session, days: int = 7) -> Dict[str, Any]:
    """Summarize cadence job runs started during the requested number of days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    rows = db.execute(
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
    ).scalars().all()

    summaries: Dict[str, Dict[str, Any]] = {}
    for run in rows:
        job = run.job or "unknown"
        summary = summaries.setdefault(
            job,
            {
                "job": job,
                "total": 0,
                "failed": 0,
                "succeeded": 0,
                "last_status": None,
                "last_started_at": None,
                "avg_rows_affected": None,
            },
        )
        summary["total"] += 1
        if run.status == "failed":
            summary["failed"] += 1
        elif run.status == "succeeded":
            summary["succeeded"] += 1

        summary["last_status"] = run.status
        summary["last_started_at"] = (
            run.started_at.isoformat() if isinstance(run.started_at, datetime) else run.started_at
        )

    for summary in summaries.values():
        job_rows = [run for run in rows if (run.job or "unknown") == summary["job"]]
        affected = [run.rows_affected for run in job_rows if run.rows_affected is not None]
        if affected:
            summary["avg_rows_affected"] = round(sum(affected) / len(affected), 2)

    return {
        "days": int(days),
        "jobs": list(summaries.values()),
    }


get_cadence_job_failures = get_failures
list_failures = get_failures


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc)
    testing_session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="alpha",
                status="failed",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
                rows_affected=3,
                detail="failed",
            ),
            CadenceJobRun(
                id=2,
                job="alpha",
                status="running",
                started_at=now - timedelta(hours=1),
                finished_at=None,
                rows_affected=None,
                detail=None,
            ),
            CadenceJobRun(
                id=3,
                job="beta",
                status="succeeded",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2),
                rows_affected=7,
                detail=None,
            ),
        ]
    )
    testing_session.commit()

    app = FastAPI()

    @app.get("/api/cadence/jobs/failures")
    def failures(days: int = 30, db: Session = Depends(get_session)):
        return get_failures(db=db, days=days)

    app.dependency_overrides[get_session] = lambda: testing_session
    with TestClient(app) as client:
        response = client.get("/api/cadence/jobs/failures?days=30")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2
    alpha = next(job for job in payload["jobs"] if job["job"] == "alpha")
    assert alpha["failed"] == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()