from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import CadenceJobRun


_OK_STATUSES = {"success", "ok", "completed", "succeeded"}
_FAILED_STATUSES = {"failure", "error", "failed"}


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    if started_at is None or finished_at is None:
        return None

    if isinstance(started_at, str):
        try:
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(finished_at, str):
        try:
            finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        except ValueError:
            return None

    if not isinstance(started_at, datetime) or not isinstance(finished_at, datetime):
        return None

    return (finished_at - started_at).total_seconds()


def _status_bucket(status: Any) -> str:
    if status is None:
        return "other"
    normalized = str(status).strip().lower()
    if normalized in _FAILED_STATUSES:
        return "failed"
    if normalized in _OK_STATUSES:
        return "ok"
    return "other"


def get_cadence_job_failure_rate(
    session: SQLAlchemySession = Depends(get_session),
    hours: int = 168,
    limit: int = 100,
) -> dict[str, Any]:
    """Return failure-rate statistics for cadence jobs in the requested window."""
    if hours <= 0:
        hours = 168
    if limit < 0:
        limit = 0

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        session.execute(
            select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
        )
        .scalars()
        .all()
    )

    jobs: dict[str, dict[str, Any]] = {}
    total_runs = 0
    total_ok = 0
    total_failed = 0

    for run in rows:
        job_name = str(run.job) if run.job is not None else ""
        bucket = _status_bucket(run.status)

        job = jobs.setdefault(
            job_name,
            {
                "job": job_name,
                "runs": 0,
                "ok": 0,
                "failed": 0,
                "other": 0,
                "_durations": [],
                "rows_affected_total": 0,
                "last_status": None,
                "last_started_at": None,
                "_last_sort": None,
            },
        )

        job["runs"] += 1
        job[bucket] += 1
        total_runs += 1
        if bucket == "ok":
            total_ok += 1
        elif bucket == "failed":
            total_failed += 1

        if run.rows_affected is not None:
            job["rows_affected_total"] += run.rows_affected

        duration = _duration_seconds(run.started_at, run.finished_at)
        if duration is not None:
            job["_durations"].append(duration)

        last_sort = run.started_at
        if job["_last_sort"] is None or (
            last_sort is not None
            and last_sort >= job["_last_sort"]
        ):
            job["_last_sort"] = last_sort
            job["last_status"] = run.status
            job["last_started_at"] = run.started_at

    result_jobs: list[dict[str, Any]] = []
    for job in jobs.values():
        durations = job.pop("_durations")
        job.pop("_last_sort", None)
        job["failure_rate"] = (
            job["failed"] / job["runs"] if job["runs"] else 0.0
        )
        job["avg_duration_seconds"] = (
            sum(durations) / len(durations) if durations else None
        )
        result_jobs.append(job)

    result_jobs.sort(key=lambda item: item["job"])
    if limit:
        result_jobs = result_jobs[:limit]

    return {
        "hours": hours,
        "jobs": result_jobs,
        "totals": {
            "runs": total_runs,
            "ok": total_ok,
            "failed": total_failed,
            "failure_rate": total_failed / total_runs if total_runs else 0.0,
        },
    }


def get_failure_rate(
    session: SQLAlchemySession = Depends(get_session),
    hours: int = 168,
    limit: int = 100,
) -> dict[str, Any]:
    return get_cadence_job_failure_rate(session=session, hours=hours, limit=limit)


def get_cadence_job_failure_rates(
    session: SQLAlchemySession = Depends(get_session),
    hours: int = 168,
    limit: int = 100,
) -> dict[str, Any]:
    return get_cadence_job_failure_rate(session=session, hours=hours, limit=limit)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    db = TestingSession()
    now = datetime.utcnow()
    db.add_all(
        [
            CadenceJobRun(
                job="indexer",
                status="success",
                started_at=now - timedelta(minutes=30),
                finished_at=now - timedelta(minutes=29),
                rows_affected=10,
                detail={"seed": True},
            ),
            CadenceJobRun(
                job="indexer",
                status="SUCCESS",
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=19),
                rows_affected=5,
                detail={"seed": True},
            ),
            CadenceJobRun(
                job="indexer",
                status="failed",
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                rows_affected=0,
                detail={"seed": True},
            ),
            CadenceJobRun(
                job="health",
                status="success",
                started_at=now - timedelta(minutes=5),
                finished_at=None,
                rows_affected=None,
                detail={"seed": True},
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()

    @test_app.get("/api/cadence/jobs/failure-rate")
    def failure_rate_endpoint(
        hours: int = 168,
        limit: int = 100,
        session: SQLAlchemySession = Depends(get_session),
    ):
        return get_cadence_job_failure_rate(
            session=session,
            hours=hours,
            limit=limit,
        )

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/cadence/jobs/failure-rate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["runs"] == 4
    assert any(
        abs(job["failure_rate"] - (1 / 3)) < 1e-9
        for job in payload["jobs"]
    )
    assert all(
        "avg_duration_seconds" in job for job in payload["jobs"]
    )
    print("PASS")
    db.close()
    engine.dispose()