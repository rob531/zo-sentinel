from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    if not isinstance(started_at, datetime) or not isinstance(finished_at, datetime):
        return None
    duration = (_utc(finished_at) - _utc(started_at)).total_seconds()
    return duration if duration >= 0 else None


def compute_rows_yield(session: Session, days: int = 7) -> dict[str, Any]:
    days = int(days)
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    runs = session.execute(select(CadenceJobRun)).scalars().all()

    grouped: dict[str, list[CadenceJobRun]] = defaultdict(list)
    for run in runs:
        if not isinstance(run.started_at, datetime):
            continue
        if _utc(run.started_at) >= cutoff:
            grouped[run.job].append(run)

    jobs: list[dict[str, Any]] = []
    for job in sorted(grouped):
        job_runs = grouped[job]
        success = sum(
            1
            for run in job_runs
            if isinstance(run.status, str) and run.status.strip().lower() == "success"
        )
        failed = len(job_runs) - success

        row_values = [
            float(run.rows_affected)
            for run in job_runs
            if run.rows_affected is not None
        ]
        total_rows = int(sum(row_values)) if row_values else 0
        mean_rows = sum(row_values) / len(row_values) if row_values else 0.0

        durations = [
            duration
            for run in job_runs
            if (duration := _duration_seconds(run.started_at, run.finished_at)) is not None
        ]
        mean_duration = sum(durations) / len(durations) if durations else 0.0

        yields = []
        for run in job_runs:
            duration = _duration_seconds(run.started_at, run.finished_at)
            if duration is None or duration == 0 or run.rows_affected is None:
                yields.append(0.0)
            else:
                yields.append(float(run.rows_affected) / duration)
        rows_per_second = sum(yields) / len(yields) if yields else 0.0

        jobs.append(
            {
                "job": job,
                "runs": len(job_runs),
                "success": success,
                "failed": failed,
                "total_rows": total_rows,
                "mean_rows": mean_rows,
                "mean_duration_seconds": mean_duration,
                "rows_per_second": rows_per_second,
            }
        )

    return {"days": days, "jobs": jobs}


def get_rows_yield(
    days: int = 7,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_rows_yield(session=session, days=days)


def get_cadence_rows_yield(
    days: int = 7,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_rows_yield(days=days, session=session)


def calculate_rows_yield(session: Session, days: int = 7) -> dict[str, Any]:
    return compute_rows_yield(session=session, days=days)


if __name__ == "__main__":
    from datetime import timedelta

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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="healthy-job",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=2),
                    rows_affected=10,
                    detail="first",
                ),
                CadenceJobRun(
                    id=2,
                    job="healthy-job",
                    status="success",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1) + timedelta(seconds=3),
                    rows_affected=30,
                    detail="second",
                ),
                CadenceJobRun(
                    id=3,
                    job="failed-job",
                    status="failed",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1) + timedelta(seconds=1),
                    rows_affected=5,
                    detail="failed",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/cadence/rows-yield")
    def rows_yield_endpoint(
        days: int = 7,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_rows_yield(days=days, session=session)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get("/api/cadence/rows-yield?days=7")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["jobs"]) == 2, payload
    jobs = {item["job"]: item for item in payload["jobs"]}
    assert jobs["healthy-job"]["mean_rows"] == 20.0, payload
    assert jobs["failed-job"]["success"] == 0, payload
    print("PASS")