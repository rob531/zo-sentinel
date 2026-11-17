from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _detail_summary(detail: Any) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, (dict, list, tuple)):
        return json.dumps(detail, separators=(",", ":"), default=str)
    return str(detail)


def get_cadence_run_duration_summary(
    session: Session = Depends(get_session),
    job: str | None = None,
    status: str | None = None,
    days: int = 7,
    limit: int = 100,
) -> dict[str, Any]:
    rows = list(session.execute(select(CadenceJobRun)).scalars().all())
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(days, 0))

    selected = []
    for row in rows:
        if job is not None and row.job != job:
            continue
        if status is not None and (row.status or "").casefold() != status.casefold():
            continue
        started_at = _as_utc(row.started_at)
        if started_at is not None and started_at < cutoff:
            continue
        selected.append(row)

    selected.sort(
        key=lambda row: (_as_utc(row.started_at) or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )

    total = len(selected)
    limited_rows = selected[: max(limit, 0)]

    runs: list[dict[str, Any]] = []
    by_job: dict[str, dict[str, Any]] = {}

    for row in limited_rows:
        started_at = _as_utc(row.started_at)
        finished_at = _as_utc(row.finished_at)
        duration_seconds: float | None = None
        if started_at is not None and finished_at is not None:
            duration_seconds = max((finished_at - started_at).total_seconds(), 0.0)

        runs.append(
            {
                "id": row.id,
                "job": row.job,
                "status": row.status,
                "started_at": row.started_at.isoformat() if isinstance(row.started_at, datetime) else row.started_at,
                "finished_at": row.finished_at.isoformat() if isinstance(row.finished_at, datetime) else row.finished_at,
                "duration_seconds": duration_seconds,
                "rows_affected": row.rows_affected,
                "detail_summary": _detail_summary(row.detail),
            }
        )

        aggregate = by_job.setdefault(
            row.job,
            {
                "total": 0,
                "failed": 0,
                "median_duration_seconds": None,
                "max_duration_seconds": None,
                "rows_affected_sum": 0,
            },
        )
        aggregate["total"] += 1
        if (row.status or "").casefold() == "failed":
            aggregate["failed"] += 1
        aggregate["rows_affected_sum"] += int(row.rows_affected or 0)

        if duration_seconds is not None:
            aggregate["max_duration_seconds"] = max(
                float(aggregate["max_duration_seconds"] or 0.0),
                duration_seconds,
            )

    for aggregate in by_job.values():
        durations = [
            run["duration_seconds"]
            for run in runs
            if run["job"] in by_job
            and run["duration_seconds"] is not None
            and by_job[run["job"]] is aggregate
        ]
        if durations:
            aggregate["median_duration_seconds"] = float(median(durations))

    stale_jobs: list[str] = []
    if days >= 0:
        latest_by_job: dict[str, datetime] = {}
        for row in rows:
            if job is not None and row.job != job:
                continue
            started_at = _as_utc(row.started_at)
            if started_at is None:
                continue
            if row.job not in latest_by_job or started_at > latest_by_job[row.job]:
                latest_by_job[row.job] = started_at
        stale_jobs = sorted(
            row_job for row_job, started_at in latest_by_job.items() if started_at < cutoff
        )

    return {
        "runs": runs,
        "total": total,
        "by_job": by_job,
        "stale_jobs": stale_jobs,
    }


get_cadence_run_duration = get_cadence_run_duration_summary
get_cadence_run_durations = get_cadence_run_duration_summary


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    seeds = [
        ("alpha", "failed", 50, 45, 0, "failed"),
        ("alpha", "success", 40, 35, 10, "complete"),
        ("beta", "success", 30, 27, 20, "complete"),
        ("beta", "success", 20, 18, 15, "complete"),
        ("beta", "success", 10, 7, 25, "complete"),
    ]
    with TestingSessionLocal() as db:
        for job_name, run_status, started_minutes, finished_minutes, affected, detail in seeds:
            db.add(
                CadenceJobRun(
                    job=job_name,
                    status=run_status,
                    started_at=base_time - timedelta(minutes=started_minutes),
                    finished_at=base_time - timedelta(minutes=finished_minutes),
                    rows_affected=affected,
                    detail=detail,
                )
            )
        db.commit()

    that_app = FastAPI()

    @that_app.get("/api/cadence/runs")
    def cadence_runs(summary: dict[str, Any] = Depends(get_cadence_run_duration_summary)) -> dict[str, Any]:
        return summary

    def override_get_session():
        with TestingSessionLocal() as db:
            yield db

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/cadence/runs", params={"days": 30, "limit": 10})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert "alpha" in payload["by_job"]
    assert "beta" in payload["by_job"]
    assert payload["by_job"]["alpha"]["failed"] == 1
    assert payload["by_job"]["alpha"]["median_duration_seconds"] is not None
    print("PASS")