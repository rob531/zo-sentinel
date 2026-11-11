from datetime import datetime, timedelta, timezone
from math import floor
from statistics import median
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * percentile
    lower = floor(position)
    upper = lower + 1
    if lower == upper:
        return float(ordered[lower])

    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _error_text(detail: Any) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for key in ("error", "message", "detail"):
            if key in detail:
                return _error_text(detail[key])
    if isinstance(detail, (list, tuple)):
        for item in detail:
            text = _error_text(item)
            if text:
                return text
    return str(detail)


def get_cadence_run_failures(
    db: Session = Depends(get_session), days: int = 14
) -> dict[str, Any]:
    days = max(1, int(days))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    rows = list(
        db.execute(
            select(CadenceJobRun)
            .where(CadenceJobRun.started_at >= cutoff)
            .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
        )
        .scalars()
        .all()
    )

    grouped: dict[str, dict[str, Any]] = {}
    total_runs = 0
    failed_runs = 0
    last_error = ""
    last_failure_at: datetime | None = None

    for row in rows:
        started_at = _utc_naive(row.started_at)
        job = grouped.setdefault(
            row.job,
            {
                "runs": 0,
                "failures": 0,
                "durations": [],
                "last_started_at": None,
            },
        )
        total_runs += 1
        job["runs"] += 1

        if job["last_started_at"] is None or started_at > job["last_started_at"]:
            job["last_started_at"] = started_at

        if row.finished_at is not None:
            finished_at = _utc_naive(row.finished_at)
            duration = (finished_at - started_at).total_seconds()
            if duration >= 0:
                job["durations"].append(float(duration))

        if row.status.lower() == "failed":
            failed_runs += 1
            job["failures"] += 1
            if last_failure_at is None or started_at > last_failure_at:
                last_failure_at = started_at
                last_error = _error_text(row.detail)

    by_job: list[dict[str, Any]] = []
    for job_name in sorted(grouped):
        job = grouped[job_name]
        durations = job["durations"]
        runs = job["runs"]
        failures = job["failures"]
        by_job.append(
            {
                "job": job_name,
                "runs": runs,
                "failures": failures,
                "failure_rate": failures / runs if runs else 0.0,
                "median_duration_s": float(median(durations)) if durations else 0.0,
                "p95_duration_s": _percentile(durations, 0.95),
                "last_started_at": _isoformat(job["last_started_at"]),
            }
        )

    return {
        "days": days,
        "total_runs": total_runs,
        "failed_runs": failed_runs,
        "failure_rate": failed_runs / total_runs if total_runs else 0.0,
        "by_job": by_job,
        "last_error": last_error,
        "last_failure_at": _isoformat(last_failure_at),
    }


def get_cadence_failures(
    db: Session = Depends(get_session), days: int = 14
) -> dict[str, Any]:
    return get_cadence_run_failures(db=db, days=days)


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
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_rows = [
        CadenceJobRun(
            job="alpha",
            status="success",
            started_at=now - timedelta(days=1),
            finished_at=now - timedelta(days=1, seconds=10),
            rows_affected=10,
            detail=None,
        ),
        CadenceJobRun(
            job="alpha",
            status="failed",
            started_at=now - timedelta(days=2),
            finished_at=now - timedelta(days=2, seconds=20),
            rows_affected=0,
            detail={"error": "alpha failed"},
        ),
        CadenceJobRun(
            job="alpha",
            status="success",
            started_at=now - timedelta(days=3),
            finished_at=None,
            rows_affected=5,
            detail=None,
        ),
        CadenceJobRun(
            job="beta",
            status="success",
            started_at=now - timedelta(days=1),
            finished_at=now - timedelta(days=1, seconds=30),
            rows_affected=20,
            detail=None,
        ),
        CadenceJobRun(
            job="beta",
            status="failed",
            started_at=now - timedelta(days=4),
            finished_at=now - timedelta(days=4, seconds=40),
            rows_affected=0,
            detail={"error": "beta failed"},
        ),
        CadenceJobRun(
            job="beta",
            status="success",
            started_at=now - timedelta(days=5),
            finished_at=now - timedelta(days=5, seconds=50),
            rows_affected=15,
            detail=None,
        ),
    ]

    with TestSession() as session:
        session.add_all(test_rows)
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/cadence/runs/failures")
    def cadence_run_failures_endpoint(
        days: int = 14, db: Session = Depends(get_session)
    ) -> dict[str, Any]:
        return get_cadence_run_failures(db=db, days=days)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/runs/failures")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_runs"] == 6
        assert payload["failed_runs"] == 2
        assert len(payload["by_job"]) == 2
        assert {item["job"]: item["failures"] for item in payload["by_job"]} == {
            "alpha": 1,
            "beta": 1,
        }

    print("PASS")