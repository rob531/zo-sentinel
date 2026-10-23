from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import get_rows_affected_rollup
except ImportError:
    get_rows_affected_rollup = None


router = APIRouter(prefix="/api", tags=["cadence"])


class JobRowsAffectedRollup(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    total_rows: int
    avg_rows: float
    avg_seconds: float
    last_finished_at: str | None


class RowsAffectedRollupResponse(BaseModel):
    jobs: list[JobRowsAffectedRollup]
    jobs_count: int
    generated_at: str


def _iso8601(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _rollup_from_db(
    session: Session,
    job: str | None,
    days: int | None,
) -> dict[str, Any]:
    filters = []
    if job is not None:
        filters.append(CadenceJobRun.job == job)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filters.append(CadenceJobRun.started_at >= cutoff)

    aggregate_stmt = (
        select(
            CadenceJobRun.job.label("job"),
            func.count(CadenceJobRun.id).label("runs"),
            func.coalesce(
                func.sum(
                    case((CadenceJobRun.status == "succeeded", 1), else_=0)
                ),
                0,
            ).label("succeeded"),
            func.coalesce(
                func.sum(
                    case((CadenceJobRun.status == "failed", 1), else_=0)
                ),
                0,
            ).label("failed"),
            func.coalesce(func.sum(CadenceJobRun.rows_affected), 0).label(
                "total_rows"
            ),
            func.coalesce(func.avg(CadenceJobRun.rows_affected), 0.0).label(
                "avg_rows"
            ),
            func.max(CadenceJobRun.finished_at).label("last_finished_at"),
        )
        .where(*filters)
        .group_by(CadenceJobRun.job)
        .order_by(CadenceJobRun.job)
    )

    duration_stmt = select(
        CadenceJobRun.job,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
    ).where(*filters)

    aggregates = session.execute(aggregate_stmt).mappings().all()
    durations: dict[str, list[float]] = {}
    for row in session.execute(duration_stmt).mappings():
        started_at = row["started_at"]
        finished_at = row["finished_at"]
        if started_at is None or finished_at is None:
            continue
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        duration = (finished_at - started_at).total_seconds()
        if duration >= 0:
            durations.setdefault(row["job"], []).append(duration)

    jobs = []
    for row in aggregates:
        job_durations = durations.get(row["job"], [])
        jobs.append(
            {
                "job": row["job"],
                "runs": int(row["runs"] or 0),
                "succeeded": int(row["succeeded"] or 0),
                "failed": int(row["failed"] or 0),
                "total_rows": int(row["total_rows"] or 0),
                "avg_rows": float(row["avg_rows"] or 0.0),
                "avg_seconds": (
                    sum(job_durations) / len(job_durations)
                    if job_durations
                    else 0.0
                ),
                "last_finished_at": _iso8601(row["last_finished_at"]),
            }
        )

    return {
        "jobs": jobs,
        "jobs_count": len(jobs),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/cadence/rows-affected",
    response_model=RowsAffectedRollupResponse,
)
async def rows_affected_rollup(
    job: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> Any:
    if get_rows_affected_rollup is not None:
        payload = get_rows_affected_rollup(session, job, days)
        if isawaitable(payload):
            payload = await payload
    else:
        payload = _rollup_from_db(session, job, days)

    if not isinstance(payload, dict):
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()
    return payload


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    rows_affected INTEGER,
                    detail TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, started_at, finished_at, rows_affected, detail)
                VALUES
                    (1, 'mcp_scanner', 'succeeded', :started, :finished, 100, NULL),
                    (2, 'mcp_scanner', 'failed', :started, :finished, 0, NULL),
                    (3, 'other_job', 'succeeded', :started, :finished, 50, NULL)
                """
            ),
            {
                "started": now - timedelta(minutes=10),
                "finished": now,
            },
        )

    session_factory = OrmSession
    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with session_factory(engine) as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(local_app) as client:
            response = client.get("/api/cadence/rows-affected")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["jobs_count"] == 2, body

            jobs = {item["job"]: item for item in body["jobs"]}
            scanner = jobs["mcp_scanner"]
            assert scanner["runs"] == 2, scanner
            assert scanner["succeeded"] == 1, scanner
            assert scanner["failed"] == 1, scanner
            assert scanner["total_rows"] == 100, scanner

            other = jobs["other_job"]
            assert other["runs"] == 1, other
            assert other["succeeded"] == 1, other
            assert other["failed"] == 0, other
            assert other["total_rows"] == 50, other
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()