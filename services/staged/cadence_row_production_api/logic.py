from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


class CadenceProductionJob(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    statuses: dict[str, int] = Field(default_factory=dict)
    total_rows_affected: int
    mean_rows_affected: float
    slowest_run_seconds: float
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None


class CadenceProductionTotals(BaseModel):
    runs: int
    succeeded: int
    failed: int
    rows_affected: int


class CadenceProductionResponse(BaseModel):
    days: int
    jobs: list[CadenceProductionJob]
    totals: CadenceProductionTotals


_SUCCESS_STATUSES = {"success", "succeeded", "ok", "completed"}
_FAILURE_STATUSES = {"failed", "failure", "error"}


def _utc_value(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(started_at: datetime, finished_at: datetime) -> float:
    duration = (_utc_value(finished_at) - _utc_value(started_at)).total_seconds()
    return max(0.0, float(duration))


def calculate_cadence_production(
    db: Session,
    days: int = 14,
    job: str | None = None,
) -> dict[str, Any]:
    window_days = max(0, int(days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    statement = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    runs = list(db.execute(statement).scalars().all())
    grouped: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    jobs: list[dict[str, Any]] = []
    total_runs = 0
    total_succeeded = 0
    total_failed = 0
    total_rows_affected = 0

    for job_name in sorted(grouped):
        job_runs = grouped[job_name]
        statuses = Counter((run.status or "unknown") for run in job_runs)
        succeeded = sum(
            count for status, count in statuses.items() if status.lower() in _SUCCESS_STATUSES
        )
        failed = sum(
            count for status, count in statuses.items() if status.lower() in _FAILURE_STATUSES
        )
        rows_affected = sum(int(run.rows_affected or 0) for run in job_runs)

        completed_runs = [
            run for run in job_runs if run.finished_at is not None and run.started_at is not None
        ]
        slowest_run_seconds = max(
            (_duration_seconds(run.started_at, run.finished_at) for run in completed_runs),
            default=0.0,
        )
        last_started_at = max(
            (run.started_at for run in job_runs if run.started_at is not None),
            key=_utc_value,
            default=None,
        )
        last_finished_at = max(
            (run.finished_at for run in job_runs if run.finished_at is not None),
            key=_utc_value,
            default=None,
        )

        jobs.append(
            {
                "job": job_name,
                "runs": len(job_runs),
                "succeeded": succeeded,
                "failed": failed,
                "statuses": dict(sorted(statuses.items())),
                "total_rows_affected": rows_affected,
                "mean_rows_affected": rows_affected / len(job_runs),
                "slowest_run_seconds": slowest_run_seconds,
                "last_started_at": last_started_at,
                "last_finished_at": last_finished_at,
            }
        )

        total_runs += len(job_runs)
        total_succeeded += succeeded
        total_failed += failed
        total_rows_affected += rows_affected

    return {
        "days": window_days,
        "jobs": jobs,
        "totals": {
            "runs": total_runs,
            "succeeded": total_succeeded,
            "failed": total_failed,
            "rows_affected": total_rows_affected,
        },
    }


def get_cadence_production(
    days: int = 14,
    job: str | None = None,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return calculate_cadence_production(db=db, days=days, job=job)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/production")
    def cadence_production_endpoint(
        days: int = 14,
        job: str | None = None,
        db: Session = Depends(get_session),
    ):
        return get_cadence_production(days=days, job=job, db=db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="bulk",
                    status="succeeded",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=2, minutes=55),
                    rows_affected=10,
                    detail="first",
                ),
                CadenceJobRun(
                    job="bulk",
                    status="succeeded",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1, minutes=58),
                    rows_affected=5,
                    detail="second",
                ),
                CadenceJobRun(
                    job="sync",
                    status="failed",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=3, minutes=59),
                    rows_affected=0,
                    detail="first",
                ),
                CadenceJobRun(
                    job="sync",
                    status="failed",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(minutes=59),
                    rows_affected=0,
                    detail="second",
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/cadence/production?days=14")
    assert response.status_code == 200
    payload = response.json()
    by_job = {item["job"]: item for item in payload["jobs"]}
    assert by_job["bulk"]["runs"] == 2
    assert by_job["bulk"]["total_rows_affected"] == 15
    assert by_job["sync"]["failed"] == 2
    assert payload["totals"]["runs"] == 4
    assert payload["totals"]["rows_affected"] == 15
    print("PASS")