from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_SUCCEEDED_STATUSES = {"succeeded", "success", "completed", "ok"}
_FAILED_STATUSES = {"failed", "failure", "error"}


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return None

    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)

    return max(0.0, (finished - started).total_seconds())


def get_cadence_job_rows_affected(
    session: Session,
    job_name: Optional[str] = None,
    limit: int = 1000,
) -> dict[str, Any]:
    query = select(CadenceJobRun)

    if job_name is not None:
        query = query.where(CadenceJobRun.job == job_name)

    query = query.order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
    query = query.limit(max(0, int(limit)))

    runs = list(session.scalars(query).all())

    grouped: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(str(run.job), []).append(run)

    jobs: list[dict[str, Any]] = []
    for job in sorted(grouped):
        job_runs = grouped[job]
        valid_rows = [
            int(run.rows_affected)
            for run in job_runs
            if run.rows_affected is not None
        ]
        durations = [
            duration
            for run in job_runs
            if (duration := _duration_seconds(run.started_at, run.finished_at))
            is not None
        ]

        total_rows_affected = sum(valid_rows)
        average_rows_affected = (
            float(total_rows_affected) / len(valid_rows) if valid_rows else 0.0
        )
        average_duration = (
            float(sum(durations) / len(durations)) if durations else 0.0
        )

        succeeded = sum(
            1
            for run in job_runs
            if str(run.status or "").strip().lower() in _SUCCEEDED_STATUSES
        )
        failed = sum(
            1
            for run in job_runs
            if str(run.status or "").strip().lower() in _FAILED_STATUSES
        )

        jobs.append(
            {
                "job": job,
                "runs": len(job_runs),
                "total_rows_affected": total_rows_affected,
                "avg_rows_affected": average_rows_affected,
                "succeeded": succeeded,
                "failed": failed,
                "avg_duration_seconds": average_duration,
            }
        )

    return {
        "jobs": jobs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_rows_affected(
    session: Session,
    job_name: Optional[str] = None,
    limit: int = 1000,
) -> dict[str, Any]:
    return get_cadence_job_rows_affected(session, job_name=job_name, limit=limit)


def get_job_rows_affected(
    session: Session,
    job_name: Optional[str] = None,
    limit: int = 1000,
) -> dict[str, Any]:
    return get_cadence_job_rows_affected(session, job_name=job_name, limit=limit)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    testing_app = FastAPI()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    testing_app.dependency_overrides[get_session] = override_get_session

    @testing_app.get("/api/cadence/jobs/rows-affected")
    def rows_affected_endpoint(
        session: Session = Depends(get_session),
        job_name: Optional[str] = None,
        limit: int = 1000,
    ):
        return get_cadence_job_rows_affected(
            session,
            job_name=job_name,
            limit=limit,
        )

    with TestingSessionLocal() as db:
        db.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="succeeded",
                    started_at=datetime(2024, 1, 1, 0, 0, 0),
                    finished_at=datetime(2024, 1, 1, 0, 0, 2),
                    rows_affected=10,
                    detail="ok",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="succeeded",
                    started_at=datetime(2024, 1, 1, 1, 0, 0),
                    finished_at=datetime(2024, 1, 1, 1, 0, 4),
                    rows_affected=20,
                    detail="ok",
                ),
                CadenceJobRun(
                    job="beta",
                    status="failed",
                    started_at=datetime(2024, 1, 1, 2, 0, 0),
                    finished_at=datetime(2024, 1, 1, 2, 0, 1),
                    rows_affected=None,
                    detail="failed",
                ),
            ]
        )
        db.commit()

    response = TestClient(testing_app).get("/api/cadence/jobs/rows-affected")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2

    by_job = {item["job"]: item for item in payload["jobs"]}
    assert by_job["alpha"]["runs"] == 2
    assert by_job["alpha"]["total_rows_affected"] == 30
    assert by_job["alpha"]["avg_rows_affected"] == 15.0
    assert by_job["alpha"]["succeeded"] == 2
    assert by_job["alpha"]["failed"] == 0
    assert by_job["alpha"]["avg_duration_seconds"] == 3.0
    assert by_job["beta"]["runs"] == 1
    assert by_job["beta"]["total_rows_affected"] == 0
    assert by_job["beta"]["failed"] == 1

    print("PASS")