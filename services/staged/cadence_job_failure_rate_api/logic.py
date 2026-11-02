"""Data and computation layer for the cadence job failure-rate API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


SUCCESS_STATUSES = {"ok", "success", "completed"}


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Normalize supported timestamp values to an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    started = _parse_datetime(started_at)
    finished = _parse_datetime(finished_at)
    if started is None or finished is None:
        return None
    return (finished - started).total_seconds()


def get_job_failure_rates(db: Session, days: int) -> List[Dict[str, Any]]:
    """Return failure-rate statistics grouped by job for the requested window."""
    if days <= 0:
        raise ValueError("days must be greater than zero")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
        )
    ).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for job, status, started_at, finished_at in rows:
        started = _parse_datetime(started_at)
        if started is None or started < cutoff:
            continue

        job_name = str(job)
        stats = grouped.setdefault(
            job_name,
            {
                "total": 0,
                "failed": 0,
                "last_run_at": None,
                "durations": [],
            },
        )
        stats["total"] += 1
        if str(status).strip().lower() not in SUCCESS_STATUSES:
            stats["failed"] += 1

        if stats["last_run_at"] is None or started > stats["last_run_at"]:
            stats["last_run_at"] = started

        duration = _duration_seconds(started_at, finished_at)
        if duration is not None:
            stats["durations"].append(duration)

    jobs: List[Dict[str, Any]] = []
    for job_name, stats in grouped.items():
        total = int(stats["total"])
        failed = int(stats["failed"])
        durations: Iterable[float] = stats["durations"]
        duration_values = list(durations)
        jobs.append(
            {
                "job": job_name,
                "total": total,
                "failed": failed,
                "failure_rate": round(failed / total, 4) if total else 0.0,
                "last_run_at": stats["last_run_at"],
                "avg_duration_seconds": (
                    sum(duration_values) / len(duration_values)
                    if duration_values
                    else None
                ),
            }
        )

    jobs.sort(key=lambda item: (-item["failure_rate"], item["job"]))
    return jobs


def get_failure_rates(db: Session, days: int) -> List[Dict[str, Any]]:
    """Compatibility name for the failure-rate computation."""
    return get_job_failure_rates(db, days)


def get_failure_rate_report(db: Session, days: int) -> Dict[str, Any]:
    """Return the complete failure-rate report payload."""
    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc),
        "jobs": get_job_failure_rates(db, days),
    }


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with testing_session() as db:
        db.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4) + timedelta(seconds=2),
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    id=2,
                    job="failing-job",
                    status="error",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3) + timedelta(seconds=3),
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    id=3,
                    job="failing-job",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=4),
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    id=4,
                    job="failing-job",
                    status="ok",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1) + timedelta(seconds=1),
                    rows_affected=1,
                    detail={},
                ),
                CadenceJobRun(
                    id=5,
                    job="healthy-job",
                    status="ok",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=1),
                    rows_affected=1,
                    detail={},
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/cadence/jobs/failure-rate")
    def failure_rate(days: int = 30, db: Session = Depends(get_session)):
        return get_failure_rate_report(db, days)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get(
        "/api/cadence/jobs/failure-rate",
        params={"days": 30},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2
    failing = next(item for item in payload["jobs"] if item["job"] == "failing-job")
    assert failing["failure_rate"] == 0.75
    assert failing["total"] == 4
    assert sum(item["failure_rate"] for item in payload["jobs"]) <= 1.0
    print("PASS")


if __name__ == "__main__":
    _self_test()