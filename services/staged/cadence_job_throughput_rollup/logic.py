"""Logic for cadence job throughput rollups over the real application database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Dict, Iterable, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CadenceJobRun


_SUCCESS_STATUSES = {
    "success",
    "succeeded",
    "ok",
    "complete",
    "completed",
    "pass",
    "passed",
}


def _duration_seconds(row: CadenceJobRun) -> float | None:
    """Return a run duration in seconds when both timestamps are available."""
    if row.started_at is None or row.finished_at is None:
        return None
    started = row.started_at
    finished = row.finished_at
    if started.tzinfo is not None and finished.tzinfo is None:
        finished = finished.replace(tzinfo=started.tzinfo)
    elif finished.tzinfo is not None and started.tzinfo is None:
        started = started.replace(tzinfo=finished.tzinfo)
    return max(0.0, (finished - started).total_seconds())


def _is_success(status: str | None) -> bool:
    return (status or "").strip().lower() in _SUCCESS_STATUSES


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def get_job_throughput_rollup(
    db: Session, window_hours: int = 24
) -> Dict[str, Any]:
    """Aggregate recent cadence job runs by job.

    The lookback is applied to ``started_at`` with a bound SQLAlchemy
    parameter, making the query portable between PostgreSQL and SQLite.
    """
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    rows = db.execute(
        select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    ).scalars().all()

    grouped: Dict[str, List[CadenceJobRun]] = {}
    for row in rows:
        grouped.setdefault(row.job, []).append(row)

    jobs: List[Dict[str, Any]] = []
    for job, job_rows in sorted(grouped.items()):
        durations = [
            duration
            for duration in (_duration_seconds(row) for row in job_rows)
            if duration is not None
        ]
        started_values = [
            row.started_at for row in job_rows if row.started_at is not None
        ]
        jobs.append(
            {
                "job": job,
                "runs": len(job_rows),
                "ok": sum(1 for row in job_rows if _is_success(row.status)),
                "failed": sum(1 for row in job_rows if not _is_success(row.status)),
                "rows_affected": sum(
                    int(row.rows_affected or 0) for row in job_rows
                ),
                "median_seconds": _median(durations),
                "p95_seconds": _p95(durations),
                "last_started_at": max(started_values) if started_values else None,
            }
        )

    return {"window_hours": window_hours, "jobs": jobs}


def get_job_throughput(
    db: Session, window_hours: int = 24
) -> Dict[str, Any]:
    """Compatibility name for callers of the throughput rollup logic."""
    return get_job_throughput_rollup(db, window_hours)


def job_throughput_rollup(
    db: Session, window_hours: int = 24
) -> Dict[str, Any]:
    """Compatibility name for callers of the throughput rollup logic."""
    return get_job_throughput_rollup(db, window_hours)


def __main__() -> None:
    """Run a small local API contract test using the real ORM model."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    CadenceJobRun.__table__.create(engine)

    db = TestingSession()
    now = datetime.utcnow()
    seed_rows = [
        CadenceJobRun(
            id=1,
            job="alpha",
            status="success",
            started_at=now - timedelta(minutes=30),
            finished_at=now - timedelta(minutes=29),
            rows_affected=10,
            detail="seed",
        ),
        CadenceJobRun(
            id=2,
            job="alpha",
            status="failed",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=18),
            rows_affected=0,
            detail="seed",
        ),
        CadenceJobRun(
            id=3,
            job="alpha",
            status="success",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=7),
            rows_affected=20,
            detail="seed",
        ),
        CadenceJobRun(
            id=4,
            job="beta",
            status="success",
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=5),
            rows_affected=5,
            detail="seed",
        ),
        CadenceJobRun(
            id=5,
            job="beta",
            status="failed",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            rows_affected=0,
            detail="seed",
        ),
    ]
    db.add_all(seed_rows)
    db.commit()
    db.close()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get("/api/cadence/job-throughput")
    def cadence_job_throughput(
        window_hours: int = 24,
        session: Session = Depends(get_session),
    ):
        return get_job_throughput_rollup(session, window_hours)

    that_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(that_app).get(
        "/api/cadence/job-throughput", params={"window_hours": 24}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["jobs"]) == 2, payload
    assert payload["window_hours"] == 24, payload
    for job in payload["jobs"]:
        assert job["ok"] + job["failed"] == job["runs"], job
        assert job["median_seconds"] >= 0, job
        assert job["p95_seconds"] >= 0, job

    print("PASS")


if __name__ == "__main__":
    __main__()