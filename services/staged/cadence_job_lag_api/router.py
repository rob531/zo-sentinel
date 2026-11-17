from __future__ import annotations

from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Mapping

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import get_cadence_job_lag as _get_cadence_job_lag
except (ImportError, ValueError):
    try:
        from .logic import get_job_lag as _get_cadence_job_lag
    except (ImportError, ValueError):
        _get_cadence_job_lag = None


router = APIRouter(prefix="/api")


class JobLagEntry(BaseModel):
    job: str
    total_runs: int
    failures: int
    last_status: str | None
    last_started_at: str | None
    last_finished_at: str | None
    last_duration_seconds: float | None
    last_rows_affected: int | None
    stale: bool
    age_seconds: float | None


class JobLagResponse(BaseModel):
    jobs: list[JobLagEntry]
    total_jobs: int
    stale_jobs: int


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = _as_utc(value)
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _duration_seconds(
    started_at: datetime | None, finished_at: datetime | None
) -> float | None:
    started = _as_utc(started_at)
    finished = _as_utc(finished_at)
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started).total_seconds())


def _read_job_lag(
    session: Session, threshold_seconds: int
) -> dict[str, Any]:
    rows = session.execute(
        select(
            CadenceJobRun.id,
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
            CadenceJobRun.detail,
        )
    ).all()

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row.job), []).append(row)

    now = datetime.now(timezone.utc)
    jobs: list[dict[str, Any]] = []

    for job_name, job_rows in grouped.items():
        failures = sum(
            1
            for row in job_rows
            if str(row.status or "").strip().lower() in {"failed", "error"}
        )

        def sort_key(row: Any) -> tuple[int, datetime, int]:
            started = _as_utc(row.started_at)
            if started is None:
                return (0, datetime.min.replace(tzinfo=timezone.utc), int(row.id))
            return (1, started, int(row.id))

        last = max(job_rows, key=sort_key)
        last_started = _as_utc(last.started_at)
        age_seconds = (
            max(0.0, (now - last_started).total_seconds())
            if last_started is not None
            else None
        )
        stale = age_seconds is not None and age_seconds > threshold_seconds

        jobs.append(
            {
                "job": job_name,
                "total_runs": len(job_rows),
                "failures": failures,
                "last_status": (
                    str(last.status) if last.status is not None else None
                ),
                "last_started_at": _iso_timestamp(last.started_at),
                "last_finished_at": _iso_timestamp(last.finished_at),
                "last_duration_seconds": _duration_seconds(
                    last.started_at, last.finished_at
                ),
                "last_rows_affected": (
                    int(last.rows_affected)
                    if last.rows_affected is not None
                    else None
                ),
                "stale": stale,
                "age_seconds": age_seconds,
            }
        )

    jobs.sort(key=lambda item: item["job"])
    return {
        "jobs": jobs,
        "total_jobs": len(jobs),
        "stale_jobs": sum(1 for item in jobs if item["stale"]),
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {
        name: getattr(value, name)
        for name in (
            "jobs",
            "total_jobs",
            "stale_jobs",
        )
        if hasattr(value, name)
    }


def _normalize_payload(
    payload: Any, threshold_seconds: int
) -> dict[str, Any]:
    mapping = _as_mapping(payload)
    raw_jobs = mapping.get("jobs") or []
    now = datetime.now(timezone.utc)
    jobs: list[dict[str, Any]] = []

    for raw_job in raw_jobs:
        job = _as_mapping(raw_job)
        started_at = job.get("last_started_at")
        if isinstance(started_at, datetime):
            started = _as_utc(started_at)
            age_seconds = (
                max(0.0, (now - started).total_seconds())
                if started is not None
                else None
            )
        elif isinstance(started_at, str) and started_at:
            try:
                parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                started = _as_utc(parsed)
                age_seconds = (
                    max(0.0, (now - started).total_seconds())
                    if started is not None
                    else None
                )
            except ValueError:
                age_seconds = job.get("age_seconds")
        else:
            age_seconds = job.get("age_seconds")

        stale = job.get("stale")
        if stale is None:
            stale = (
                age_seconds is not None and age_seconds > threshold_seconds
            )

        failures = job.get("failures")
        if failures is None:
            failures = job.get("failure_count", 0)

        jobs.append(
            {
                "job": str(job.get("job") or ""),
                "total_runs": int(job.get("total_runs") or 0),
                "failures": int(failures or 0),
                "last_status": (
                    str(job["last_status"])
                    if job.get("last_status") is not None
                    else None
                ),
                "last_started_at": _iso_timestamp(job.get("last_started_at")),
                "last_finished_at": _iso_timestamp(job.get("last_finished_at")),
                "last_duration_seconds": (
                    float(job["last_duration_seconds"])
                    if job.get("last_duration_seconds") is not None
                    else None
                ),
                "last_rows_affected": (
                    int(job["last_rows_affected"])
                    if job.get("last_rows_affected") is not None
                    else None
                ),
                "stale": bool(stale),
                "age_seconds": (
                    float(age_seconds) if age_seconds is not None else None
                ),
            }
        )

    jobs.sort(key=lambda item: item["job"])
    return {
        "jobs": jobs,
        "total_jobs": len(jobs),
        "stale_jobs": sum(1 for item in jobs if item["stale"]),
    }


@router.get("/cadence/job-lag", response_model=JobLagResponse)
async def cadence_job_lag(
    threshold_seconds: int = Query(default=3600, ge=0),
    session: Session = Depends(get_session),
) -> JobLagResponse:
    payload: Any = None

    if _get_cadence_job_lag is not None:
        try:
            payload = _get_cadence_job_lag(
                session, threshold_seconds=threshold_seconds
            )
        except TypeError:
            payload = _get_cadence_job_lag(session, threshold_seconds)
        if isawaitable(payload):
            payload = await payload

    if payload is None:
        payload = _read_job_lag(session, threshold_seconds)

    return JobLagResponse(**_normalize_payload(payload, threshold_seconds))


if __name__ == "__main__":
    from datetime import timedelta

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seeded_rows = [
        CadenceJobRun(
            id=1,
            job="daily_scan",
            status="success",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=3) + timedelta(minutes=4),
            rows_affected=100,
            detail="completed",
        ),
        CadenceJobRun(
            id=2,
            job="daily_scan",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2) + timedelta(minutes=1),
            rows_affected=0,
            detail="failed",
        ),
        CadenceJobRun(
            id=3,
            job="daily_scan",
            status="success",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=18),
            rows_affected=125,
            detail="completed",
        ),
        CadenceJobRun(
            id=4,
            job="threat_refresh",
            status="success",
            started_at=now - timedelta(hours=5),
            finished_at=now - timedelta(hours=5) + timedelta(minutes=2),
            rows_affected=50,
            detail="completed",
        ),
        CadenceJobRun(
            id=5,
            job="threat_refresh",
            status="success",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=4) + timedelta(minutes=2),
            rows_affected=60,
            detail="completed",
        ),
        CadenceJobRun(
            id=6,
            job="threat_refresh",
            status="success",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1) + timedelta(minutes=3),
            rows_affected=75,
            detail="completed",
        ),
    ]

    with TestSession() as test_session:
        test_session.add_all(seeded_rows)
        test_session.commit()

        def override_get_session():
            with TestSession() as session:
                yield session

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(test_app).get("/api/cadence/job-lag")
        payload = response.json()
        assert response.status_code == 200
        assert payload["total_jobs"] == 2
        assert any(job["failures"] == 1 for job in payload["jobs"])
        assert any(
            job["last_duration_seconds"] is not None for job in payload["jobs"]
        )
        assert all(isinstance(job["stale"], bool) for job in payload["jobs"])

    print("PASS")