from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {
    "success",
    "succeeded",
    "ok",
    "complete",
    "completed",
}
_MAX_LIMIT = 1000


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _timestamp(value: Any) -> str | None:
    parsed = _as_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _duration_seconds(started_at: Any, finished_at: Any) -> float:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return 0.0
    try:
        duration = (finished - started).total_seconds()
    except TypeError:
        started = started.replace(tzinfo=None)
        finished = finished.replace(tzinfo=None)
        duration = (finished - started).total_seconds()
    return float(max(0.0, duration))


def _detail_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _run_payload(run: CadenceJobRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "started_at": _timestamp(run.started_at),
        "finished_at": _timestamp(run.finished_at),
        "duration_s": _duration_seconds(run.started_at, run.finished_at),
        "rows_affected": run.rows_affected,
        "detail": _detail_value(run.detail),
    }


def list_jobs(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return the cadence jobs and their most recent activity."""
    runs = (
        session.query(CadenceJobRun)
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .all()
    )
    cutoff = _utc_now_naive() - timedelta(hours=24)
    grouped: dict[str, list[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    jobs = []
    for job in sorted(grouped):
        job_runs = grouped[job]
        latest = job_runs[0]
        runs_24h = 0
        for run in job_runs:
            started = _as_datetime(run.started_at)
            if started is not None and started >= cutoff:
                runs_24h += 1
        jobs.append(
            {
                "job": job,
                "last_status": latest.status,
                "last_started_at": _timestamp(latest.started_at),
                "runs_24h": runs_24h,
            }
        )

    return {"jobs": jobs}


def get_job_runs(
    job_name: str,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return recent runs for one job and a trailing seven-day summary."""
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = 50
    normalized_limit = max(1, min(normalized_limit, _MAX_LIMIT))

    runs = (
        session.query(CadenceJobRun)
        .filter(CadenceJobRun.job == job_name)
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .all()
    )

    cutoff = _utc_now_naive() - timedelta(days=7)
    window_runs = []
    for run in runs:
        started = _as_datetime(run.started_at)
        if started is not None and started >= cutoff:
            window_runs.append(run)

    success = sum(1 for run in window_runs if run.status in _SUCCESS_STATUSES)
    failure = len(window_runs) - success
    durations = [
        _duration_seconds(run.started_at, run.finished_at)
        for run in window_runs
    ]

    return {
        "job": job_name,
        "limit": normalized_limit,
        "summary": {
            "runs": len(window_runs),
            "success": success,
            "failure": failure,
            "success_rate": float(success / len(window_runs)) if window_runs else 0.0,
            "median_duration_s": float(median(durations)) if durations else 0.0,
            "rows_affected_total": sum(
                int(run.rows_affected or 0) for run in window_runs
            ),
            "window_days": 7,
        },
        "runs": [_run_payload(run) for run in runs[:normalized_limit]],
    }


# Compatibility aliases for the common service-logic naming conventions.
get_cadence_jobs = list_jobs
list_cadence_jobs = list_jobs
get_job_run_detail = get_job_runs
get_cadence_job_runs = get_job_runs
list_cadence_job_runs = get_job_runs


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine)

    now = _utc_now_naive()
    with TestingSession() as db:
        db.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29, seconds=30),
                    rows_affected=10,
                    detail={"source": "test"},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="error",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9, seconds=30),
                    rows_affected=0,
                    detail={"source": "test", "error": "example"},
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=5),
                    rows_affected=3,
                    detail={"source": "test"},
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3) + timedelta(seconds=5),
                    rows_affected=2,
                    detail={"source": "test"},
                ),
            ]
        )
        db.commit()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/cadence/jobs")
    def _list_jobs(session: Session = Depends(get_session)):
        return list_jobs(session=session)

    @that_app.get("/api/cadence/jobs/{job_name}/runs")
    def _get_job_runs(
        job_name: str,
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        return get_job_runs(job_name=job_name, limit=limit, session=session)

    client = TestClient(that_app)
    jobs_response = client.get("/api/cadence/jobs")
    assert jobs_response.status_code == 200
    jobs_payload = jobs_response.json()
    assert {item["job"] for item in jobs_payload["jobs"]} == {"alpha", "beta"}

    runs_response = client.get("/api/cadence/jobs/alpha/runs?limit=10")
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert runs_payload["summary"]["runs"] == 2
    assert runs_payload["summary"]["failure"] == 1
    assert runs_payload["summary"]["success_rate"] == 0.5
    assert isinstance(runs_payload["runs"][0]["duration_s"], float)
    assert runs_payload["runs"][0]["duration_s"] >= 0.0

    print("PASS")