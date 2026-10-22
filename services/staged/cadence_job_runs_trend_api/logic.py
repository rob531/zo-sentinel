from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

__all__ = [
    "aggregate_cadence_job_runs_trend",
    "get_cadence_job_runs_trend",
    "get_trend",
    "get_trend_data",
]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(started_at: datetime, finished_at: datetime) -> float:
    started = _as_utc(started_at)
    finished = _as_utc(finished_at)
    return (finished - started).total_seconds()


def _aggregate_rows(rows: list[Any], days: int, job: str | None) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    cutoff_date = today - timedelta(days=days - 1)
    cutoff = datetime.combine(cutoff_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=timezone.utc)

    job_rows: dict[str, list[Any]] = {}
    series_counts: dict[tuple[date, str], int] = {}

    for row in rows:
        started_at = row.started_at
        if started_at is None:
            continue

        started = _as_utc(started_at)
        if started < cutoff or started >= end:
            continue

        run_job = str(row.job)
        status = str(row.status) if row.status is not None else "unknown"
        run_date = started.date()

        job_rows.setdefault(run_job, []).append(row)
        series_counts[(run_date, status)] = series_counts.get((run_date, status), 0) + 1

    jobs: list[dict[str, Any]] = []
    for run_job in sorted(job_rows):
        entries = job_rows[run_job]
        durations = [
            _duration_seconds(entry.started_at, entry.finished_at)
            for entry in entries
            if entry.started_at is not None and entry.finished_at is not None
        ]
        rows_affected = sum(
            int(entry.rows_affected or 0)
            for entry in entries
        )
        jobs.append(
            {
                "job": run_job,
                "runs": len(entries),
                "succeeded": sum(1 for entry in entries if entry.status == "succeeded"),
                "failed": sum(1 for entry in entries if entry.status == "failed"),
                "rows_affected": rows_affected,
                "avg_duration_seconds": (
                    sum(durations) / len(durations) if durations else None
                ),
            }
        )

    series = [
        {"date": run_date.isoformat(), "status": status, "count": count}
        for (run_date, status), count in sorted(series_counts.items())
    ]

    return {"days": days, "jobs": jobs, "series": series}


def aggregate_cadence_job_runs_trend(
    session: Session,
    days: int = 14,
    job: str | None = None,
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    statement = select(
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
        CadenceJobRun.rows_affected,
    )
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    rows = list(session.execute(statement).all())
    return _aggregate_rows(rows, days, job)


def get_cadence_job_runs_trend(
    session: Session = Depends(get_session),
    days: int = 14,
    job: str | None = None,
) -> dict[str, Any]:
    return aggregate_cadence_job_runs_trend(session=session, days=days, job=job)


get_trend = get_cadence_job_runs_trend
get_trend_data = get_cadence_job_runs_trend


def run() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.metadata.create_all(engine)

    from sqlalchemy.orm import sessionmaker

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    yesterday = now - timedelta(days=1)

    seeded_rows = [
        CadenceJobRun(
            job="scanner",
            status="failed",
            started_at=yesterday,
            finished_at=yesterday + timedelta(seconds=30),
            rows_affected=0,
            detail={"seeded": True},
        ),
        CadenceJobRun(
            job="scanner",
            status="succeeded",
            started_at=now,
            finished_at=now + timedelta(seconds=60),
            rows_affected=10,
            detail={"seeded": True},
        ),
        CadenceJobRun(
            job="indexer",
            status="succeeded",
            started_at=yesterday,
            finished_at=yesterday + timedelta(seconds=45),
            rows_affected=20,
            detail={"seeded": True},
        ),
        CadenceJobRun(
            job="indexer",
            status="succeeded",
            started_at=now,
            finished_at=now + timedelta(seconds=50),
            rows_affected=25,
            detail={"seeded": True},
        ),
        CadenceJobRun(
            job="reporter",
            status="succeeded",
            started_at=yesterday,
            finished_at=yesterday + timedelta(seconds=20),
            rows_affected=5,
            detail={"seeded": True},
        ),
        CadenceJobRun(
            job="reporter",
            status="succeeded",
            started_at=now,
            finished_at=None,
            rows_affected=7,
            detail={"seeded": True},
        ),
    ]
    testing_session.add_all(seeded_rows)
    testing_session.commit()

    def override_get_session():
        yield testing_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/cadence/runs/trend")
    def trend_endpoint(
        days: int = Query(14, ge=1),
        job: str | None = None,
        session: Session = Depends(get_session),
    ):
        return get_cadence_job_runs_trend(session=session, days=days, job=job)

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/runs/trend", params={"days": 2})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 2
        assert len(payload["jobs"]) == 3
        assert len(payload["series"]) == len(
            {(entry["date"], entry["status"]) for entry in payload["series"]}
        )
        failed = [
            entry
            for entry in payload["series"]
            if entry["status"] == "failed"
            and entry["date"] == yesterday.date().isoformat()
        ]
        assert len(failed) == 1
        assert failed[0]["count"] == 1
        scanner = next(entry for entry in payload["jobs"] if entry["job"] == "scanner")
        assert scanner["avg_duration_seconds"] is not None

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run()