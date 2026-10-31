from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def get_cadence_job_volume(
    days: int,
    session: Optional[Session] = None,
    *,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    data_session = session or db
    if data_session is None:
        raise ValueError("a database session is required")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - __import__(
        "datetime"
    ).timedelta(days=days)

    statement = (
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
        )
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.job, CadenceJobRun.started_at, CadenceJobRun.id)
    )
    records = data_session.execute(statement).all()

    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        job = str(record.job)
        status = str(record.status or "").strip().lower()

        item = grouped.setdefault(
            job,
            {
                "job": job,
                "runs": 0,
                "ok_runs": 0,
                "failed_runs": 0,
                "rows_affected_sum": 0,
                "duration_total": 0.0,
                "duration_count": 0,
                "max_duration_seconds": None,
                "last_started_at": None,
            },
        )

        item["runs"] += 1
        if status == "ok":
            item["ok_runs"] += 1
        elif status == "error":
            item["failed_runs"] += 1

        if record.rows_affected is not None:
            item["rows_affected_sum"] += int(record.rows_affected)

        started_at = _as_datetime(record.started_at)
        finished_at = _as_datetime(record.finished_at)
        if started_at is not None:
            if item["last_started_at"] is None or started_at > item["last_started_at"]:
                item["last_started_at"] = started_at

        if started_at is not None and finished_at is not None:
            duration = (_as_utc_naive(finished_at) - _as_utc_naive(started_at)).total_seconds()
            item["duration_total"] += duration
            item["duration_count"] += 1
            current_max = item["max_duration_seconds"]
            if current_max is None or duration > current_max:
                item["max_duration_seconds"] = duration

    jobs = []
    for item in sorted(grouped.values(), key=lambda value: value["job"]):
        duration_count = item["duration_count"]
        jobs.append(
            {
                "job": item["job"],
                "runs": item["runs"],
                "ok_runs": item["ok_runs"],
                "failed_runs": item["failed_runs"],
                "rows_affected_sum": item["rows_affected_sum"],
                "avg_duration_seconds": (
                    item["duration_total"] / duration_count if duration_count else None
                ),
                "max_duration_seconds": item["max_duration_seconds"],
                "last_started_at": item["last_started_at"],
            }
        )

    return {"days": days, "jobs": jobs}


def get_job_volume(
    days: int,
    session: Optional[Session] = None,
    *,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    return get_cadence_job_volume(days, session, db=db)


def get_cadence_job_volume_stats(
    days: int,
    session: Optional[Session] = None,
    *,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    return get_cadence_job_volume(days, session, db=db)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI, Query
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
                    job="alpha",
                    status="ok",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=5,
                    detail=None,
                ),
                CadenceJobRun(
                    id=2,
                    job="alpha",
                    status="ok",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=7,
                    detail=None,
                ),
                CadenceJobRun(
                    id=3,
                    job="beta",
                    status="error",
                    started_at=now - timedelta(minutes=2),
                    finished_at=None,
                    rows_affected=None,
                    detail="failure",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/cadence/job-volume")
    def cadence_job_volume_endpoint(
        days: int = Query(..., ge=1, le=365),
        session: Session = Depends(get_session),
    ):
        return get_cadence_job_volume(days=days, session=session)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/cadence/job-volume", params={"days": 7})

    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 7
    assert len(payload["jobs"]) == 2
    jobs = {job["job"]: job for job in payload["jobs"]}
    assert jobs["alpha"]["failed_runs"] == 0
    assert jobs["beta"]["failed_runs"] == 1
    print("PASS")