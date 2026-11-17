from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _iso_datetime(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    started = _parse_datetime(started_at)
    finished = _parse_datetime(finished_at)
    if started is None or finished is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return float((finished - started).total_seconds())


def compute_job_timeline(runs: list[dict]) -> dict[str, Any]:
    normalized_runs = []
    for run in runs:
        started_at = run.get("started_at")
        finished_at = run.get("finished_at")
        detail = run.get("detail")
        normalized_runs.append(
            {
                "id": int(run["id"]) if run.get("id") is not None else None,
                "job": str(run.get("job") or ""),
                "status": str(run.get("status") or ""),
                "started_at": _iso_datetime(started_at),
                "finished_at": _iso_datetime(finished_at),
                "rows_affected": (
                    int(run["rows_affected"])
                    if run.get("rows_affected") is not None
                    else None
                ),
                "duration_seconds": _duration_seconds(started_at, finished_at),
                "detail": dict(detail) if isinstance(detail, dict) else {},
            }
        )
    return {"total_runs": len(normalized_runs), "runs": normalized_runs}


def get_job_timeline(
    session: Session,
    job: str,
    limit: int = 50,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 500))
    job_filter = CadenceJobRun.job == job
    total_runs = int(
        session.execute(
            select(func.count(CadenceJobRun.id)).where(job_filter)
        ).scalar_one()
    )
    rows = session.execute(
        select(CadenceJobRun)
        .where(job_filter)
        .order_by(CadenceJobRun.id.desc())
        .limit(bounded_limit)
    ).scalars().all()
    run_dicts = [
        {
            "id": row.id,
            "job": row.job,
            "status": row.status,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "rows_affected": row.rows_affected,
            "detail": row.detail,
        }
        for row in rows
    ]
    timeline = compute_job_timeline(run_dicts)
    return {
        "job": job,
        "total_runs": total_runs,
        "runs": timeline["runs"],
    }


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/cadence/jobs/{job}/timeline")
    def job_timeline_endpoint(
        job: str,
        limit: int = Query(default=50, ge=1, le=500),
        session: Session = Depends(get_session),
    ):
        return get_job_timeline(session, job, limit)

    test_app.dependency_overrides[get_session] = override_get_session
    started_at = datetime(2025, 1, 1, 12, 0, 0)
    with testing_session() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="scanner",
                    status="ok",
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=2),
                    rows_affected=10,
                    detail={"message": "completed"},
                ),
                CadenceJobRun(
                    job="scanner",
                    status="failed",
                    started_at=started_at + timedelta(minutes=1),
                    finished_at=started_at + timedelta(minutes=1, seconds=3),
                    rows_affected=0,
                    detail={"message": "failed"},
                ),
                CadenceJobRun(
                    job="other",
                    status="ok",
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=1),
                    rows_affected=1,
                    detail={"message": "other"},
                ),
            ]
        )
        session.commit()
        highest_scanner_id = session.execute(
            select(func.max(CadenceJobRun.id)).where(CadenceJobRun.job == "scanner")
        ).scalar_one()

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/cadence/jobs/scanner/timeline")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["job"] == "scanner"
            assert payload["total_runs"] == 2
            assert len(payload["runs"]) == 2
            assert payload["runs"][0]["id"] == highest_scanner_id
            duration = payload["runs"][0]["duration_seconds"]
            assert duration is None or isinstance(duration, float)
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")