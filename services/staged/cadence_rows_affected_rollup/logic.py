"""logic.py -- the service's data/computation layer (one concern, one file).

Reads the real cadence job history through the application SQLAlchemy session.
The router is responsible for HTTP concerns and response-model validation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CadenceJobRun


DEFAULT_DAYS = 14
MIN_DAYS = 1
MAX_DAYS = 90


def _clamp_days(days: int) -> int:
    """Clamp the requested reporting window to the supported range."""
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, value))


def _is_success(status: Any) -> bool:
    return str(status or "").strip().lower() in {"success", "succeeded"}


def _is_failure(status: Any) -> bool:
    return str(status or "").strip().lower() in {
        "failure",
        "failed",
        "error",
        "errored",
    }


def rows_affected_rollup(
    db: Session,
    job: Optional[str] = None,
    days: int = DEFAULT_DAYS,
) -> Dict[str, Any]:
    """Return cadence throughput statistics for the requested time window.

    The time window is based on ``started_at``. Only the real
    ``cadence_job_runs`` application table is queried.
    """
    window_days = _clamp_days(days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    statement = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    if job is not None and str(job).strip():
        statement = statement.where(CadenceJobRun.job == job)

    statement = statement.order_by(
        CadenceJobRun.started_at.desc(),
        CadenceJobRun.id.desc(),
    )
    runs = list(db.execute(statement).scalars().all())

    grouped: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        job_name = run.job
        item = grouped.setdefault(
            job_name,
            {
                "job": job_name,
                "run_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "total_rows_affected": 0,
                "_row_values": [],
                "last_status": None,
                "last_started_at": None,
            },
        )
        item["run_count"] += 1
        if _is_success(run.status):
            item["success_count"] += 1
        if _is_failure(run.status):
            item["failure_count"] += 1
        if run.rows_affected is not None:
            item["total_rows_affected"] += int(run.rows_affected)
            item["_row_values"].append(int(run.rows_affected))

        # Rows are ordered newest first, so the first row for a job is its
        # most recent run. The id tie-breaker makes the result deterministic.
        if item["last_started_at"] is None:
            item["last_status"] = run.status
            item["last_started_at"] = run.started_at

    jobs = []
    total_runs = 0
    total_rows = 0
    total_failures = 0

    for job_name in sorted(grouped):
        item = grouped[job_name]
        values = item.pop("_row_values")
        item["avg_rows_affected"] = (
            float(sum(values) / len(values)) if values else 0.0
        )
        item["max_rows_affected"] = max(values) if values else 0
        jobs.append(item)
        total_runs += item["run_count"]
        total_rows += item["total_rows_affected"]
        total_failures += item["failure_count"]

    return {
        "days": window_days,
        "jobs": jobs,
        "totals": {
            "runs": total_runs,
            "rows_affected": total_rows,
            "failures": total_failures,
        },
    }


def get_rows_affected_rollup(
    db: Session,
    job: Optional[str] = None,
    days: int = DEFAULT_DAYS,
) -> Dict[str, Any]:
    """Compatibility name for the primary rollup function."""
    return rows_affected_rollup(db, job=job, days=days)


def get_rollup(
    db: Session,
    job: Optional[str] = None,
    days: int = DEFAULT_DAYS,
) -> Dict[str, Any]:
    """Compatibility name for callers using the generic rollup name."""
    return rows_affected_rollup(db, job=job, days=days)


def get_rows_affected(
    db: Session,
    job: Optional[str] = None,
    days: int = DEFAULT_DAYS,
) -> Dict[str, Any]:
    """Compatibility name for the rows-affected aggregation."""
    return rows_affected_rollup(db, job=job, days=days)


def build_rows_affected_rollup(
    db: Session,
    job: Optional[str] = None,
    days: int = DEFAULT_DAYS,
) -> Dict[str, Any]:
    """Compatibility name for the rows-affected aggregation."""
    return rows_affected_rollup(db, job=job, days=days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)

    now = datetime.now(timezone.utc)
    seeded = [
        CadenceJobRun(
            id=1,
            job="alpha",
            status="success",
            started_at=now - timedelta(days=1),
            finished_at=now - timedelta(days=1),
            rows_affected=10,
            detail="seed",
        ),
        CadenceJobRun(
            id=2,
            job="alpha",
            status="failure",
            started_at=now - timedelta(days=2),
            finished_at=now - timedelta(days=2),
            rows_affected=4,
            detail="seed",
        ),
        CadenceJobRun(
            id=3,
            job="alpha",
            status="success",
            started_at=now - timedelta(days=3),
            finished_at=now - timedelta(days=3),
            rows_affected=7,
            detail="seed",
        ),
        CadenceJobRun(
            id=4,
            job="beta",
            status="success",
            started_at=now - timedelta(days=1),
            finished_at=now - timedelta(days=1),
            rows_affected=6,
            detail="seed",
        ),
        CadenceJobRun(
            id=5,
            job="beta",
            status="success",
            started_at=now - timedelta(days=4),
            finished_at=now - timedelta(days=4),
            rows_affected=3,
            detail="seed",
        ),
    ]

    def override_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/cadence/rows-affected")
    def test_endpoint(days: int = DEFAULT_DAYS, job: Optional[str] = None):
        return rows_affected_rollup(
            next(override_session()),
            job=job,
            days=days,
        )

    test_app.dependency_overrides[get_session] = override_session

    with Session(engine) as session:
        session.add_all(seeded)
        session.commit()

    response = TestClient(test_app).get("/api/cadence/rows-affected?days=14")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2
    assert sum(job["total_rows_affected"] for job in payload["jobs"]) == 30
    alpha = next(job for job in payload["jobs"] if job["job"] == "alpha")
    assert alpha["failure_count"] == 1
    print("PASS")