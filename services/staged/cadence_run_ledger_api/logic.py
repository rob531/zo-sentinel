from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 500


def _clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(MIN_LIMIT, min(MAX_LIMIT, value))


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _iso8601(value: Any) -> str | None:
    parsed = _as_datetime(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    started = _as_datetime(started_at)
    finished = _as_datetime(finished_at)
    if started is None or finished is None:
        return None

    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)

    return max(0.0, (finished - started).total_seconds())


def list_cadence_runs(
    job: str | None = None,
    limit: int = DEFAULT_LIMIT,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    limit = _clamp_limit(limit)

    statement = select(
        CadenceJobRun.id,
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
        CadenceJobRun.rows_affected,
    )
    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    statement = statement.order_by(
        CadenceJobRun.started_at.desc(),
        CadenceJobRun.id.desc(),
    ).limit(limit)

    rows = session.execute(statement).mappings().all()

    runs: list[dict[str, Any]] = []
    job_summaries: dict[str, dict[str, Any]] = {}
    failures = 0

    for row in rows:
        run_job = str(row["job"])
        status = str(row["status"])
        duration = _duration_seconds(row["started_at"], row["finished_at"])
        is_failure = status.casefold() == "failed"

        runs.append(
            {
                "id": int(row["id"]),
                "job": run_job,
                "status": status,
                "started_at": _iso8601(row["started_at"]),
                "finished_at": _iso8601(row["finished_at"]),
                "duration_seconds": duration,
                "rows_affected": int(row["rows_affected"] or 0),
            }
        )

        summary = job_summaries.setdefault(
            run_job,
            {
                "job": run_job,
                "runs": 0,
                "failures": 0,
                "duration_total": 0.0,
                "duration_count": 0,
            },
        )
        summary["runs"] += 1
        if is_failure:
            summary["failures"] += 1
            failures += 1
        if duration is not None:
            summary["duration_total"] += duration
            summary["duration_count"] += 1

    jobs: list[dict[str, Any]] = []
    for summary in sorted(job_summaries.values(), key=lambda item: item["job"]):
        duration_count = summary["duration_count"]
        jobs.append(
            {
                "job": summary["job"],
                "runs": int(summary["runs"]),
                "failures": int(summary["failures"]),
                "avg_duration_seconds": (
                    float(summary["duration_total"] / duration_count)
                    if duration_count
                    else None
                ),
            }
        )

    return {
        "runs": runs,
        "total": len(runs),
        "jobs": jobs,
        "failures": failures,
    }


get_cadence_runs = list_cadence_runs
get_runs = list_cadence_runs


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    class RunItem(BaseModel):
        id: int
        job: str
        status: str
        started_at: str | None
        finished_at: str | None
        duration_seconds: float | None
        rows_affected: int

    class JobSummary(BaseModel):
        job: str
        runs: int
        failures: int
        avg_duration_seconds: float | None

    class LedgerResponse(BaseModel):
        runs: list[RunItem]
        total: int
        jobs: list[JobSummary]
        failures: int

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with test_session() as db:
        db.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="alpha",
                    status="success",
                    started_at=base,
                    finished_at=base.replace(second=2),
                    rows_affected=4,
                    detail="completed",
                ),
                CadenceJobRun(
                    id=2,
                    job="beta",
                    status="success",
                    started_at=base.replace(hour=1),
                    finished_at=base.replace(hour=1, second=1),
                    rows_affected=2,
                    detail="completed",
                ),
                CadenceJobRun(
                    id=3,
                    job="beta",
                    status="failed",
                    started_at=base.replace(hour=2),
                    finished_at=base.replace(hour=2, second=1),
                    rows_affected=0,
                    detail="failed",
                ),
            ]
        )
        db.commit()

    local_app = FastAPI()

    @local_app.get("/api/cadence/runs", response_model=LedgerResponse)
    def cadence_runs(
        job: str | None = None,
        limit: int = DEFAULT_LIMIT,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return list_cadence_runs(job=job, limit=limit, session=db)

    def override_session():
        with test_session() as db:
            yield db

    local_app.dependency_overrides[get_session] = override_session

    with TestClient(local_app) as client:
        response = client.get("/api/cadence/runs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["failures"] == 1
        assert len(payload["jobs"]) == 2
        assert len(payload["runs"]) == 3

    print("PASS")


if __name__ == "__main__":
    _self_test()