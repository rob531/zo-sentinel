"""logic.py -- cadence run failure analysis over the real application data layer."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _duration_seconds(run: CadenceJobRun) -> float:
    """Return a run's duration, treating an unfinished run as zero seconds."""
    if run.finished_at is None:
        return 0.0
    duration = (run.finished_at - run.started_at).total_seconds()
    return max(0.0, float(duration))


def get_run_failures(db: Session, days: int = 30) -> Dict[str, object]:
    """Compute per-job cadence run failure statistics for the last ``days`` days."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(0, days)

    cutoff = datetime.utcnow() - timedelta(days=days)
    runs = db.execute(
        select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    ).scalars().all()

    grouped: Dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job or "", []).append(run)

    jobs = []
    total_runs = 0
    total_failed = 0
    candidates = []

    for job_name in sorted(grouped):
        job_runs = grouped[job_name]
        failed = sum(1 for run in job_runs if run.status == "failed")
        completed = [run for run in job_runs if run.finished_at is not None]
        durations = [_duration_seconds(run) for run in job_runs]
        average = (
            sum(_duration_seconds(run) for run in completed) / len(completed)
            if completed
            else 0.0
        )
        maximum = max(durations) if durations else 0.0
        rows_affected = sum(int(run.rows_affected or 0) for run in job_runs)

        failure_rate = failed / len(job_runs) if job_runs else 0.0
        job_result = {
            "job": job_name,
            "runs": len(job_runs),
            "failed": failed,
            "failure_rate": float(failure_rate),
            "avg_seconds": float(average),
            "max_seconds": float(maximum),
            "rows_affected": int(rows_affected),
        }
        jobs.append(job_result)

        total_runs += len(job_runs)
        total_failed += failed
        if failed:
            candidates.append((failure_rate, failed, len(job_runs), job_name))

    if candidates:
        worst_job = max(candidates, key=lambda item: (item[0], item[1], item[2]))[3]
    else:
        worst_job = ""

    return {
        "days": days,
        "jobs": jobs,
        "totals": {
            "runs": total_runs,
            "failed": total_failed,
            "worst_job": worst_job,
        },
    }


# Compatibility aliases for routers and direct contract consumers.
get_cadence_run_failures = get_run_failures
get_cadence_failure_stats = get_run_failures
get_cadence_run_failure_stats = get_run_failures
cadence_run_failures = get_run_failures


if __name__ == "__main__":
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
    CadenceJobRun.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/cadence/run-failures")
    def cadence_run_failures_route(days: int = 30, db: Session = Depends(get_session)):
        return get_run_failures(db, days)

    now = datetime.utcnow()
    with testing_session() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(days=1),
                    finished_at=now - timedelta(days=1) + timedelta(seconds=10),
                    rows_affected=4,
                    detail="first failure",
                ),
                CadenceJobRun(
                    id=2,
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(days=1, hours=12),
                    finished_at=now - timedelta(days=1, hours=12) + timedelta(seconds=20),
                    rows_affected=6,
                    detail="second failure",
                ),
                CadenceJobRun(
                    id=3,
                    job="mixed-job",
                    status="success",
                    started_at=now - timedelta(hours=18),
                    finished_at=now - timedelta(hours=18) + timedelta(seconds=30),
                    rows_affected=8,
                    detail="successful run",
                ),
                CadenceJobRun(
                    id=4,
                    job="mixed-job",
                    status="skipped",
                    started_at=now - timedelta(days=1, hours=6),
                    finished_at=None,
                    rows_affected=0,
                    detail="unfinished run",
                ),
                CadenceJobRun(
                    id=5,
                    job="healthy-job",
                    status="success",
                    started_at=now - timedelta(hours=12),
                    finished_at=now - timedelta(hours=12) + timedelta(seconds=50),
                    rows_affected=10,
                    detail="healthy run",
                ),
            ]
        )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/run-failures?days=2")
        assert response.status_code == 200
        payload = response.json()
        failing = next(item for item in payload["jobs"] if item["job"] == "failing-job")
        assert failing["failure_rate"] == 1.0
        assert abs(failing["avg_seconds"] - 15.0) <= 1.0
        assert payload["totals"]["worst_job"] == "failing-job"

    print("PASS")