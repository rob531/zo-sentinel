"""Logic for cadence job duration reporting over the real application models."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


class JobDurationStats(BaseModel):
    job: str
    runs: int
    failures: int
    failure_rate: float
    median_duration_s: float
    mean_duration_s: float
    last_status: Optional[str]
    last_started_at: Optional[str]
    rows_affected_total: int


class JobDurationTotals(BaseModel):
    jobs: int
    runs: int
    failures: int


class JobDurationReport(BaseModel):
    window_hours: int
    jobs: List[JobDurationStats]
    totals: JobDurationTotals


router = APIRouter(prefix="/api")


def _is_success(status: Optional[str]) -> bool:
    return (status or "").strip().lower() in {"success", "succeeded", "ok"}


def _duration_seconds(started_at: datetime, finished_at: datetime) -> float:
    if started_at.tzinfo is None and finished_at.tzinfo is not None:
        finished_at = finished_at.replace(tzinfo=None)
    elif finished_at.tzinfo is None and started_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=None)
    return max(0.0, (finished_at - started_at).total_seconds())


def _sort_timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def get_job_duration_report(
    db: Session,
    job: Optional[str] = None,
    limit: int = 50,
    window_hours: int = 168,
) -> Dict:
    """Build per-job duration and failure statistics from recent cadence runs."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0, window_hours))
    statement = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    rows = list(db.scalars(statement).all())
    grouped: Dict[str, List[CadenceJobRun]] = {}
    for run in rows:
        grouped.setdefault(run.job, []).append(run)

    reports: List[JobDurationStats] = []
    total_runs = 0
    total_failures = 0

    for job_name, job_rows in grouped.items():
        completed = [run for run in job_rows if run.finished_at is not None]
        failures = sum(
            1
            for run in job_rows
            if not _is_success(run.status) or run.finished_at is None
        )
        successful_durations = [
            _duration_seconds(run.started_at, run.finished_at)
            for run in completed
            if _is_success(run.status)
        ]
        latest = max(
            job_rows,
            key=lambda run: (_sort_timestamp(run.started_at), int(run.id or 0)),
        )
        run_count = len(completed)
        failure_rate = failures / run_count if run_count else 0.0
        rows_affected_total = sum(int(run.rows_affected or 0) for run in job_rows)

        reports.append(
            JobDurationStats(
                job=job_name,
                runs=run_count,
                failures=failures,
                failure_rate=failure_rate,
                median_duration_s=float(median(successful_durations))
                if successful_durations
                else 0.0,
                mean_duration_s=(
                    sum(successful_durations) / len(successful_durations)
                    if successful_durations
                    else 0.0
                ),
                last_status=latest.status,
                last_started_at=latest.started_at.isoformat(),
                rows_affected_total=rows_affected_total,
            )
        )
        total_runs += run_count
        total_failures += failures

    reports.sort(
        key=lambda item: (
            _sort_timestamp(datetime.fromisoformat(item.last_started_at))
            if item.last_started_at
            else 0.0,
            item.job,
        ),
        reverse=True,
    )

    return {
        "window_hours": window_hours,
        "jobs": [item.model_dump() for item in reports[: max(0, limit)]],
        "totals": {
            "jobs": len(reports),
            "runs": total_runs,
            "failures": total_failures,
        },
    }


@router.get("/cadence/job-duration-report", response_model=JobDurationReport)
def cadence_job_duration_report(
    job: Optional[str] = None,
    limit: int = 50,
    window_hours: int = 168,
    db: Session = Depends(get_session),
) -> Dict:
    return get_job_duration_report(
        db=db,
        job=job,
        limit=limit,
        window_hours=window_hours,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as test_db:
        test_db.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="example",
                    status="success",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4, seconds=-10),
                    rows_affected=10,
                    detail="first success",
                ),
                CadenceJobRun(
                    id=2,
                    job="example",
                    status="succeeded",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3, seconds=-20),
                    rows_affected=20,
                    detail="second success",
                ),
                CadenceJobRun(
                    id=3,
                    job="example",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2, seconds=-5),
                    rows_affected=5,
                    detail="failure",
                ),
                CadenceJobRun(
                    id=4,
                    job="example",
                    status="success",
                    started_at=now - timedelta(hours=1),
                    finished_at=None,
                    rows_affected=0,
                    detail="unfinished run",
                ),
            ]
        )
        test_db.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with TestSession() as test_db:
            yield test_db

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/cadence/job-duration-report",
            params={"job": "example", "window_hours": 168},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["jobs"]) == 1, payload
        entry = payload["jobs"][0]
        assert entry["runs"] == 3, payload
        assert entry["failures"] == 2, payload
        assert entry["median_duration_s"] == 15.0, payload
        assert entry["mean_duration_s"] == 15.0, payload

    test_engine.dispose()
    print("PASS")