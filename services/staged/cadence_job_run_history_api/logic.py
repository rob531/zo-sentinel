from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Depends
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


SUCCESS_STATUSES = {
    "success",
    "succeeded",
    "successful",
    "complete",
    "completed",
    "ok",
    "passed",
}


def _success_condition():
    return func.lower(CadenceJobRun.status).in_(SUCCESS_STATUSES)


def _failure_condition():
    return or_(
        CadenceJobRun.status.is_(None),
        func.lower(CadenceJobRun.status).notin_(SUCCESS_STATUSES),
    )


def get_cadence_job_run_history(
    session: Session | None = None,
    job: str | None = None,
    days: int = 14,
    db: Session | None = None,
) -> dict[str, Any]:
    """Return cadence run statistics and fleet-wide job names."""
    db_session = session or db
    if db_session is None:
        raise ValueError("A database session is required")

    try:
        day_count = int(days)
    except (TypeError, ValueError):
        day_count = 14
    if day_count < 1:
        day_count = 1

    bound = datetime.now(timezone.utc) - timedelta(days=day_count)

    aggregate_stmt = (
        select(
            CadenceJobRun.job.label("job"),
            func.count(CadenceJobRun.id).label("total_runs"),
            func.sum(case((_success_condition(), 1), else_=0)).label("successes"),
            func.sum(case((_failure_condition(), 1), else_=0)).label("failures"),
            func.coalesce(func.sum(CadenceJobRun.rows_affected), 0).label(
                "rows_affected"
            ),
        )
        .where(CadenceJobRun.started_at >= bound)
        .group_by(CadenceJobRun.job)
        .order_by(CadenceJobRun.job)
    )
    if job is not None:
        aggregate_stmt = aggregate_stmt.where(CadenceJobRun.job == job)

    aggregate_rows = db_session.execute(aggregate_stmt).all()

    rows_stmt = (
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= bound)
        .order_by(
            CadenceJobRun.job,
            CadenceJobRun.started_at.desc(),
            CadenceJobRun.id.desc(),
        )
    )
    if job is not None:
        rows_stmt = rows_stmt.where(CadenceJobRun.job == job)
    runs = db_session.execute(rows_stmt).scalars().all()

    durations: dict[str, list[float]] = {}
    latest: dict[str, tuple[str | None, datetime | None]] = {}
    for run in runs:
        name = run.job
        if run.started_at is not None and run.finished_at is not None:
            duration = (run.finished_at - run.started_at).total_seconds()
            if duration >= 0:
                durations.setdefault(name, []).append(duration)
        if name not in latest:
            latest[name] = (run.status, run.started_at)

    jobs = []
    for row in aggregate_rows:
        name = row.job
        job_durations = durations.get(name, [])
        total_runs = int(row.total_runs or 0)
        successes = int(row.successes or 0)
        failures = int(row.failures or 0)
        success_rate = (successes / total_runs * 100.0) if total_runs else 0.0
        last_status, last_started_at = latest.get(name, (None, None))
        jobs.append(
            {
                "job": name,
                "total_runs": total_runs,
                "successes": successes,
                "failures": failures,
                "success_rate_pct": success_rate,
                "rows_affected": int(row.rows_affected or 0),
                "avg_duration_seconds": (
                    sum(job_durations) / len(job_durations)
                    if job_durations
                    else 0.0
                ),
                "last_status": last_status,
                "last_started_at": last_started_at,
            }
        )

    all_rows = db_session.execute(
        select(CadenceJobRun).order_by(
            CadenceJobRun.job,
            CadenceJobRun.started_at.desc(),
            CadenceJobRun.id.desc(),
        )
    ).scalars().all()

    job_names = sorted({run.job for run in all_rows if run.job is not None})
    latest_statuses = {}
    for run in all_rows:
        if run.job is not None and run.job not in latest_statuses:
            latest_statuses[run.job] = {
                "status": run.status,
                "started_at": run.started_at,
            }

    return {
        "jobs": jobs,
        "job_names": job_names,
        "latest_job_statuses": latest_statuses,
    }


def get_cadence_job_history(
    db: Session | None = None,
    job: str | None = None,
    days: int = 14,
    session: Session | None = None,
) -> dict[str, Any]:
    return get_cadence_job_run_history(
        session=session or db, job=job, days=days
    )


def get_job_run_history(
    db: Session | None = None,
    job: str | None = None,
    days: int = 14,
    session: Session | None = None,
) -> dict[str, Any]:
    return get_cadence_job_run_history(
        session=session or db, job=job, days=days
    )


def get_cadence_job_summary(
    db: Session | None = None,
    job: str | None = None,
    days: int = 14,
    session: Session | None = None,
) -> dict[str, Any]:
    return get_cadence_job_run_history(
        session=session or db, job=job, days=days
    )


def get_cadence_job_names(
    db: Session | None = None, session: Session | None = None
) -> list[str]:
    db_session = session or db
    if db_session is None:
        raise ValueError("A database session is required")
    rows = db_session.execute(
        select(CadenceJobRun.job).where(CadenceJobRun.job.is_not(None)).distinct()
    ).scalars().all()
    return sorted(rows)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    CadenceJobRun.__table__.create(test_engine)
    test_db = TestSession()
    test_db.add_all(
        [
            CadenceJobRun(
                job="alpha",
                status="success",
                started_at=datetime.now(timezone.utc) - timedelta(hours=3),
                finished_at=datetime.now(timezone.utc) - timedelta(hours=3, seconds=-1),
                rows_affected=10,
                detail="ok",
            ),
            CadenceJobRun(
                job="alpha",
                status="failed",
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
                finished_at=datetime.now(timezone.utc) - timedelta(hours=2, seconds=-1),
                rows_affected=0,
                detail="failed",
            ),
            CadenceJobRun(
                job="beta",
                status="success",
                started_at=datetime.now(timezone.utc) - timedelta(hours=1),
                finished_at=datetime.now(timezone.utc) - timedelta(hours=1, seconds=-1),
                rows_affected=5,
                detail="ok",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/jobs/runs")
    def cadence_runs(
        job: str | None = None,
        days: int = 14,
        db: Session = Depends(get_session),
    ):
        return get_cadence_job_run_history(db, job, days)

    response = TestClient(test_app).get(
        "/api/cadence/jobs/runs", params={"job": "alpha", "days": 14}
    )
    assert response.status_code == 200
    payload = response.json()
    alpha = next(item for item in payload["jobs"] if item["job"] == "alpha")
    assert alpha["failures"] == 1
    assert alpha["success_rate_pct"] == 50.0
    assert "alpha" in payload["job_names"]
    assert "beta" in payload["job_names"]
    print("PASS")