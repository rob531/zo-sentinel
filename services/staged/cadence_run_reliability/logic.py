"""Logic layer for cadence run reliability reporting."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


def _utcnow() -> datetime:
    return datetime.utcnow()


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def get_reliability_data(
    db: Session,
    days: int = 7,
    job: str = "",
    status: str = "",
) -> Dict[str, Any]:
    """Summarise cadence job runs within the requested time window."""
    window_days = max(1, min(int(days), 90))
    now = _utcnow()
    cutoff = now - timedelta(days=window_days)

    statement = select(CadenceJobRun).where(
        CadenceJobRun.started_at >= cutoff,
        CadenceJobRun.started_at <= now,
    )
    if job:
        statement = statement.where(CadenceJobRun.job == job)
    if status:
        statement = statement.where(CadenceJobRun.status == status)

    rows = db.execute(statement).scalars().all()
    grouped: Dict[str, list[CadenceJobRun]] = {}
    for row in rows:
        grouped.setdefault(row.job or "", []).append(row)

    jobs: list[Dict[str, Any]] = []
    total_runs = 0
    total_ok = 0
    total_failed = 0

    for job_name in sorted(grouped):
        job_rows = grouped[job_name]
        runs = len(job_rows)
        ok = sum(
            1 for row in job_rows if (row.status or "").lower() in {"ok", "success"}
        )
        failed = sum(
            1
            for row in job_rows
            if (row.status or "").lower() in {"failed", "error"}
        )
        durations: list[float] = []
        for row in job_rows:
            started = _as_datetime(row.started_at)
            finished = _as_datetime(row.finished_at)
            if started is not None and finished is not None:
                durations.append((finished - started).total_seconds())

        durations.sort()
        median_duration = durations[len(durations) // 2] if durations else None
        last_started = max(
            (_as_datetime(row.started_at) for row in job_rows),
            key=lambda value: value if value is not None else datetime.min,
            default=None,
        )
        total_rows = sum(int(row.rows_affected or 0) for row in job_rows)

        jobs.append(
            {
                "job": job_name,
                "runs": runs,
                "ok": ok,
                "failed": failed,
                "success_rate": round(100.0 * ok / runs, 2) if runs else 0.0,
                "median_duration_s": median_duration,
                "total_rows": total_rows,
                "last_started_at": last_started.isoformat() if last_started else None,
            }
        )
        total_runs += runs
        total_ok += ok
        total_failed += failed

    return {
        "days": window_days,
        "jobs": jobs,
        "totals": {
            "runs": total_runs,
            "failed": total_failed,
            "success_rate": (
                round(100.0 * total_ok / total_runs, 2) if total_runs else 0.0
            ),
        },
    }


def get_cadence_reliability(
    db: Session,
    days: int = 7,
    job: str = "",
    status: str = "",
) -> Dict[str, Any]:
    return get_reliability_data(db, days=days, job=job, status=status)


def get_cadence_reliability_data(
    db: Session,
    days: int = 7,
    job: str = "",
    status: str = "",
) -> Dict[str, Any]:
    return get_reliability_data(db, days=days, job=job, status=status)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    started = _utcnow() - timedelta(minutes=5)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="alpha",
                    status="ok",
                    started_at=started,
                    finished_at=started + timedelta(seconds=2),
                    rows_affected=10,
                ),
                CadenceJobRun(
                    id=2,
                    job="alpha",
                    status="failed",
                    started_at=started + timedelta(minutes=1),
                    finished_at=started + timedelta(minutes=1, seconds=3),
                    rows_affected=0,
                ),
                CadenceJobRun(
                    id=3,
                    job="beta",
                    status="ok",
                    started_at=started + timedelta(minutes=2),
                    finished_at=started + timedelta(minutes=2, seconds=4),
                    rows_affected=7,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/cadence/reliability")
    def reliability(days: int = 7, job: str = "", status: str = ""):
        return get_reliability_data(
            session,
            days=days,
            job=job,
            status=status,
        )

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session
    response = TestClient(test_app).get("/api/cadence/reliability?days=7")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 2
    failed_job = next(item for item in payload["jobs"] if item["job"] == "alpha")
    assert failed_job["failed"] == 1
    assert failed_job["success_rate"] < 100
    assert payload["totals"]["runs"] == 3
    print("PASS")