from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil, floor
from typing import Any

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {"success", "succeeded", "ok", "complete", "completed"}
_FAILURE_STATUSES = {"failed", "failure", "error", "errored"}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = floor(position)
    upper = ceil(position)

    if lower == upper:
        return float(ordered[lower])

    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _duration_seconds(started_at: datetime, finished_at: datetime) -> float | None:
    duration = (finished_at - started_at).total_seconds()
    return duration if duration >= 0 else None


def get_cadence_job_duration_rollup(session: Any, days: int = 7) -> dict[str, Any]:
    if days is None:
        days = 7
    days = int(days)
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        session.query(
            CadenceJobRun.id,
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
            CadenceJobRun.detail,
        )
        .filter(CadenceJobRun.started_at >= cutoff)
        .all()
    )

    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        job_name = row.job
        job = grouped.setdefault(
            job_name,
            {
                "durations": [],
                "ok": 0,
                "failed": 0,
                "rows_affected_total": 0,
                "last_started_at": None,
            },
        )

        status = str(row.status or "").strip().casefold()
        if status in _SUCCESS_STATUSES:
            job["ok"] += 1
        elif status in _FAILURE_STATUSES:
            job["failed"] += 1

        job["rows_affected_total"] += int(row.rows_affected or 0)

        if job["last_started_at"] is None or row.started_at > job["last_started_at"]:
            job["last_started_at"] = row.started_at

        if row.finished_at is not None:
            duration = _duration_seconds(row.started_at, row.finished_at)
            if duration is not None:
                job["durations"].append(duration)

    jobs: list[dict[str, Any]] = []
    totals = {"runs": 0, "failed": 0, "rows_affected": 0}

    for job_name in sorted(grouped):
        job = grouped[job_name]
        durations = job["durations"]
        runs = len(job["durations"]) + sum(
            1
            for row in rows
            if row.job == job_name
            and row.finished_at is None
        )

        jobs.append(
            {
                "job": job_name,
                "runs": runs,
                "ok": job["ok"],
                "failed": job["failed"],
                "p50_seconds": _percentile(durations, 50),
                "p90_seconds": _percentile(durations, 90),
                "max_seconds": max(durations) if durations else 0.0,
                "rows_affected_total": job["rows_affected_total"],
                "last_started_at": job["last_started_at"],
            }
        )

        totals["runs"] += runs
        totals["failed"] += job["failed"]
        totals["rows_affected"] += job["rows_affected_total"]

    return {"days": days, "jobs": jobs, "totals": totals}


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
    TestingSession = sessionmaker(bind=engine)
    CadenceJobRun.__table__.create(engine)

    now = datetime.now(timezone.utc)
    seed_rows = [
        (1, "single-run", "success", 12.5, 3),
        (2, "multi-run", "success", 1.0, 1),
        (3, "multi-run", "success", 2.0, 2),
        (4, "multi-run", "failed", 3.0, 0),
        (5, "multi-run", "success", 4.0, 4),
    ]

    with TestingSession() as session:
        for run_id, job, status, duration, affected in seed_rows:
            started_at = now - timedelta(minutes=run_id)
            session.add(
                CadenceJobRun(
                    id=run_id,
                    job=job,
                    status=status,
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=duration),
                    rows_affected=affected,
                    detail="self-test",
                )
            )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/job-duration-rollup")
    def cadence_job_duration_rollup(days: int = 7, session=Depends(get_session)):
        return get_cadence_job_duration_rollup(session, days)

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/job-duration-rollup")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        assert payload["totals"]["runs"] == 5

        single_run = next(job for job in payload["jobs"] if job["job"] == "single-run")
        assert single_run["p50_seconds"] == 12.5
        assert single_run["max_seconds"] == 12.5

    print("PASS")