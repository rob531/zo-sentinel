from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


BUCKETS = {
    "0-60": 0,
    "60-300": 1,
    "300-1800": 2,
    "1800-7200": 3,
    ">7200": 4,
}


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _duration_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return None

    if started.tzinfo is not None and finished.tzinfo is None:
        finished = finished.replace(tzinfo=started.tzinfo)
    elif started.tzinfo is None and finished.tzinfo is not None:
        started = started.replace(tzinfo=finished.tzinfo)

    return (finished - started).total_seconds()


def _bucket_for(duration: float) -> str:
    if duration < 60:
        return "0-60"
    if duration < 300:
        return "60-300"
    if duration < 1800:
        return "300-1800"
    if duration <= 7200:
        return "1800-7200"
    return ">7200"


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def compute_duration_histogram(
    session: Session,
    days: int = 30,
    job: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute recent completed cadence-job duration distributions.

    Date filtering and timestamp interval calculations are performed in Python
    so the same logic works with PostgreSQL and SQLite timestamp values.
    """
    days = max(1, int(days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_naive = cutoff.replace(tzinfo=None)

    rows = session.execute(select(CadenceJobRun)).scalars().all()
    grouped: Dict[str, List[float]] = {}
    failures: Dict[str, int] = {}

    for row in rows:
        row_job = getattr(row, "job", None)
        if row_job is None:
            continue
        if job is not None and row_job != job:
            continue

        finished = _as_datetime(getattr(row, "finished_at", None))
        if finished is None:
            continue
        if finished.tzinfo is not None:
            finished_for_cutoff = finished.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            finished_for_cutoff = finished
        if finished_for_cutoff < cutoff_naive:
            continue

        duration = _duration_seconds(
            getattr(row, "started_at", None),
            getattr(row, "finished_at", None),
        )
        if duration is None:
            continue

        grouped.setdefault(row_job, []).append(duration)
        if getattr(row, "status", None) != "success":
            failures[row_job] = failures.get(row_job, 0) + 1

    jobs: List[Dict[str, Any]] = []
    for row_job in sorted(grouped):
        durations = grouped[row_job]
        counts = {bucket: 0 for bucket in BUCKETS}
        for duration in durations:
            counts[_bucket_for(duration)] += 1

        jobs.append(
            {
                "job": row_job,
                "total": len(durations),
                "buckets": counts,
                "median_seconds": float(median(durations)),
                "p95_seconds": _percentile(durations, 0.95),
                "failure_count": failures.get(row_job, 0),
            }
        )

    return {"days": days, "jobs": jobs}


def get_job_duration_histogram(
    session: Session,
    days: int = 30,
    job: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_duration_histogram(session, days=days, job=job)


def get_histogram(
    session: Session,
    days: int = 30,
    job: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_duration_histogram(session, days=days, job=job)


def build_duration_histogram(
    session: Session,
    days: int = 30,
    job: Optional[str] = None,
) -> Dict[str, Any]:
    return compute_duration_histogram(session, days=days, job=job)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(bind=engine)
    local_session = Session(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeded = [
        ("alpha", "success", 10),
        ("alpha", "success", 120),
        ("alpha", "success", 900),
        ("alpha", "success", 10000),
        ("beta", "failed", 60),
    ]
    for name, status, duration in seeded:
        started = now - timedelta(seconds=duration)
        local_session.add(
            CadenceJobRun(
                job=name,
                status=status,
                started_at=started,
                finished_at=now,
                rows_affected=0,
                detail="self-test",
            )
        )
    local_session.commit()

    local_app = FastAPI()

    @local_app.get("/api/cadence/jobs/duration-histogram")
    def duration_histogram(
        days: int = 30,
        job: Optional[str] = None,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return compute_duration_histogram(db, days=days, job=job)

    def override_session() -> Any:
        yield local_session

    local_app.dependency_overrides[get_session] = override_session
    client = TestClient(local_app)
    response = client.get("/api/cadence/jobs/duration-histogram?days=30&job=alpha")
    assert response.status_code == 200
    payload = response.json()
    alpha = payload["jobs"][0]
    assert alpha["total"] == 4
    assert alpha["buckets"] == {
        "0-60": 1,
        "60-300": 1,
        "300-1800": 1,
        "1800-7200": 0,
        ">7200": 1,
    }
    assert alpha["median_seconds"] == 510.0
    assert alpha["failure_count"] == 0

    failed = client.get("/api/cadence/jobs/duration-histogram?days=30&job=beta")
    assert failed.status_code == 200
    assert failed.json()["jobs"][0]["failure_count"] == 1

    local_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()