"""Logic for the cadence failure taxonomy service."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    if started_at is None or finished_at is None:
        return None

    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    if isinstance(finished_at, str):
        finished_at = datetime.fromisoformat(finished_at)

    try:
        return (finished_at - started_at).total_seconds()
    except TypeError:
        return None


def get_failure_taxonomy(db: Session) -> Dict[str, List[Dict[str, Any]]]:
    """Return per-job cadence run statistics from the real application store."""
    runs = db.execute(select(CadenceJobRun)).scalars().all()
    grouped: Dict[str, List[CadenceJobRun]] = {}

    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    jobs: List[Dict[str, Any]] = []
    for job in sorted(grouped):
        job_runs = grouped[job]
        total_runs = len(job_runs)
        failed_runs = sum(1 for run in job_runs if run.status == "failed")
        ok_runs = [run for run in job_runs if run.status == "ok"]
        durations = [
            duration
            for run in job_runs
            if (duration := _duration_seconds(run.started_at, run.finished_at)) is not None
        ]

        jobs.append(
            {
                "job": job,
                "total_runs": total_runs,
                "failed_runs": failed_runs,
                "failure_rate": failed_runs / total_runs if total_runs else 0.0,
                "ok_rows_affected": sum(run.rows_affected or 0 for run in ok_runs),
                "avg_duration_s": (
                    sum(durations) / len(durations) if durations else 0.0
                ),
                "last_run_at": max(
                    (run.started_at for run in job_runs if run.started_at is not None),
                    default=None,
                ),
            }
        )

    return {"jobs": jobs}


failure_taxonomy = get_failure_taxonomy
get_cadence_failure_taxonomy = get_failure_taxonomy


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    CadenceJobRun.__table__.create(bind=engine)

    session = TestingSession()
    base = datetime(2025, 1, 1, 12, 0, 0)
    session.add_all(
        [
            CadenceJobRun(
                id=1,
                job="failing-job",
                status="failed",
                started_at=base,
                finished_at=base + timedelta(seconds=10),
                rows_affected=0,
                detail="failure",
            ),
            CadenceJobRun(
                id=2,
                job="failing-job",
                status="ok",
                started_at=base + timedelta(minutes=1),
                finished_at=base + timedelta(minutes=1, seconds=20),
                rows_affected=7,
                detail="success",
            ),
            CadenceJobRun(
                id=3,
                job="other-job",
                status="ok",
                started_at=base + timedelta(minutes=2),
                finished_at=base + timedelta(minutes=2, seconds=5),
                rows_affected=11,
                detail="success",
            ),
            CadenceJobRun(
                id=4,
                job="other-job",
                status="running",
                started_at=base + timedelta(minutes=3),
                finished_at=None,
                rows_affected=None,
                detail="running",
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    def override_session():
        yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/cadence/failure-taxonomy")
    def failure_taxonomy_endpoint(db: Session = Depends(get_session)):
        return get_failure_taxonomy(db)

    response = TestClient(test_app).get("/api/cadence/failure-taxonomy")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2
    failing = next(job for job in payload["jobs"] if job["job"] == "failing-job")
    assert failing["failed_runs"] == 1
    assert failing["failure_rate"] == 0.5
    assert sum(job["ok_rows_affected"] for job in payload["jobs"]) == 18

    session.close()
    print("PASS")