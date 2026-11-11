from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    if started_at is None or finished_at is None:
        return None

    def coerce(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    started = coerce(started_at)
    finished = coerce(finished_at)
    if started is None or finished is None:
        return None

    if (started.tzinfo is None) != (finished.tzinfo is None):
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)

    return (finished - started).total_seconds()


def _is_success(status: str) -> bool:
    normalized = status.casefold()
    return any(token in normalized for token in ("ok", "success", "complete"))


def _is_failure(status: str) -> bool:
    normalized = status.casefold()
    return "fail" in normalized or "error" in normalized


def get_recent_runs(
    db: Session | None = None,
    job: str | None = None,
    limit: int = 20,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    active_session = session if session is not None else db
    if active_session is None:
        raise ValueError("A database session is required")

    try:
        bounded_limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        bounded_limit = 20

    ranked = (
        select(
            CadenceJobRun.id,
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
            CadenceJobRun.detail,
            func.row_number()
            .over(
                partition_by=CadenceJobRun.job,
                order_by=CadenceJobRun.id.desc(),
            )
            .label("run_rank"),
        )
        .cte("ranked_runs")
    )

    if job is not None:
        ranked = ranked.where(ranked.c.job == job)

    windowed = (
        select(ranked)
        .where(ranked.c.run_rank <= 20)
        .cte("windowed_runs")
    )

    latest = (
        select(
            windowed.c.job.label("job"),
            func.max(windowed.c.id).label("last_id"),
        )
        .group_by(windowed.c.job)
        .order_by(func.max(windowed.c.id).desc())
        .limit(bounded_limit)
        .cte("latest_jobs")
    )

    rows = (
        active_session.execute(
            select(windowed)
            .join(latest, windowed.c.id == latest.c.last_id)
            .order_by(latest.c.last_id.desc())
        )
        .mappings()
        .all()
    )

    summaries: list[dict[str, Any]] = []
    total_failures = 0

    for row in rows:
        window_rows = (
            active_session.execute(
                select(windowed).where(
                    windowed.c.job == row["job"],
                    windowed.c.run_rank <= 20,
                )
            )
            .mappings()
            .all()
        )

        successes = 0
        failures = 0
        rows_affected_total = 0

        for window_row in window_rows:
            status = str(window_row["status"] or "")
            if _is_success(status):
                successes += 1
            elif _is_failure(status):
                failures += 1
            rows_affected_total += int(window_row["rows_affected"] or 0)

        last_status = str(row["status"] or "")
        total_failures += failures
        summaries.append(
            {
                "job": str(row["job"]),
                "last_status": last_status,
                "last_started_at": (
                    row["started_at"].isoformat()
                    if isinstance(row["started_at"], datetime)
                    else row["started_at"]
                ),
                "last_finished_at": (
                    row["finished_at"].isoformat()
                    if isinstance(row["finished_at"], datetime)
                    else row["finished_at"]
                ),
                "last_duration_seconds": _duration_seconds(
                    row["started_at"], row["finished_at"]
                ),
                "last_rows_affected": int(row["rows_affected"] or 0),
                "runs": len(window_rows),
                "successes": successes,
                "failures": failures,
                "rows_affected_total": rows_affected_total,
            }
        )

    return {
        "jobs": summaries,
        "totals": {
            "jobs": len(summaries),
            "failures": total_failures,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


get_cadence_job_recent_runs = get_recent_runs
get_recent_job_runs = get_recent_runs
get_recent_cadence_job_runs = get_recent_runs


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as test_session:
        test_session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="failing-job",
                    status="failed",
                    started_at=datetime(2025, 1, 1, 10, 0, 0),
                    finished_at=datetime(2025, 1, 1, 10, 0, 2),
                    rows_affected=3,
                    detail="failure",
                ),
                CadenceJobRun(
                    id=2,
                    job="healthy-job",
                    status="ok",
                    started_at=datetime(2025, 1, 1, 11, 0, 0),
                    finished_at=datetime(2025, 1, 1, 11, 0, 1),
                    rows_affected=5,
                    detail="success",
                ),
                CadenceJobRun(
                    id=3,
                    job="healthy-job",
                    status="ok",
                    started_at=datetime(2025, 1, 1, 12, 0, 0),
                    finished_at=datetime(2025, 1, 1, 12, 0, 3),
                    rows_affected=7,
                    detail="success",
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/jobs/runs")
    def test_recent_runs(
        job: str | None = None,
        limit: int = 20,
        session: Session = Depends(get_session),
    ):
        return get_recent_runs(session=session, job=job, limit=limit)

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/jobs/runs")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        failing = next(
            item for item in payload["jobs"] if item["job"] == "failing-job"
        )
        assert failing["failures"] == 1
        assert failing["successes"] == 0
        assert payload["totals"]["failures"] == 1

    print("PASS")