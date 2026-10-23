from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import get_cadence_run_history as _logic_get_cadence_run_history
except ImportError:
    try:
        from .logic import get_cadence_runs as _logic_get_cadence_run_history
    except ImportError:
        try:
            from .logic import get_run_history as _logic_get_cadence_run_history
        except ImportError:
            _logic_get_cadence_run_history = None


router = APIRouter(prefix="/api/cadence", tags=["cadence-run-history"])


class CadenceRun(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    rows_affected: int | None = None


class CadenceRunSummary(BaseModel):
    by_status: dict[str, int]
    total: int


class CadenceRunHistoryResponse(BaseModel):
    runs: list[CadenceRun]
    summary: CadenceRunSummary


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    if started_at is None or finished_at is None:
        return None
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    if isinstance(finished_at, str):
        finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    if started_at.tzinfo is not None and finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=started_at.tzinfo)
    elif started_at.tzinfo is None and finished_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=finished_at.tzinfo)
    return (finished_at - started_at).total_seconds()


def _query_runs(session: Session, job: str | None, limit: int) -> list[dict[str, Any]]:
    ranked = (
        select(
            CadenceJobRun.id.label("id"),
            CadenceJobRun.job.label("job"),
            CadenceJobRun.status.label("status"),
            CadenceJobRun.started_at.label("started_at"),
            CadenceJobRun.finished_at.label("finished_at"),
            CadenceJobRun.rows_affected.label("rows_affected"),
            func.row_number()
            .over(
                partition_by=CadenceJobRun.job,
                order_by=(desc(CadenceJobRun.started_at), desc(CadenceJobRun.id)),
            )
            .label("job_rank"),
        )
        .where(CadenceJobRun.job == job)
        .subquery()
    )
    statement = (
        select(ranked)
        .where(ranked.c.job_rank <= limit)
        .order_by(desc(ranked.c.started_at), desc(ranked.c.id))
    )
    return [dict(row) for row in session.execute(statement).mappings().all()]


def _value(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _call_logic(session: Session, job: str | None, limit: int) -> Any:
    loader = _logic_get_cadence_run_history
    if loader is None:
        return None

    signature = inspect.signature(loader)
    parameters = signature.parameters
    kwargs: dict[str, Any] = {}

    for name in ("session", "db"):
        if name in parameters:
            kwargs[name] = session
            break
    if "job" in parameters:
        kwargs["job"] = job
    if "limit" in parameters:
        kwargs["limit"] = limit

    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        kwargs = {"session": session, "job": job, "limit": limit}

    return loader(**kwargs)


def _normalize_payload(payload: Any, session: Session, job: str | None, limit: int) -> dict[str, Any]:
    rows: Any = payload
    if isinstance(payload, tuple) and len(payload) == 2:
        rows = payload[0]
    elif isinstance(payload, dict):
        rows = payload.get("runs", [])

    if not isinstance(rows, (list, tuple)):
        rows = _query_runs(session, job, limit)

    normalized: list[dict[str, Any]] = []
    for row in rows:
        started_at = _value(row, "started_at")
        finished_at = _value(row, "finished_at")
        duration = _value(row, "duration_seconds")
        if duration is None:
            duration = _duration_seconds(started_at, finished_at)
        normalized.append(
            {
                "id": _value(row, "id"),
                "job": _value(row, "job"),
                "status": _value(row, "status"),
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": duration,
                "rows_affected": _value(row, "rows_affected"),
            }
        )

    by_status = {"ok": 0, "failed": 0, "running": 0}
    for row in normalized:
        status = str(row["status"]).lower()
        if status in by_status:
            by_status[status] += 1

    return {
        "runs": normalized,
        "summary": {"by_status": by_status, "total": len(normalized)},
    }


@router.get("/runs", response_model=CadenceRunHistoryResponse)
def cadence_run_history(
    job: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        payload = _call_logic(session, job, limit)
    except (AttributeError, TypeError):
        payload = None
    if payload is None:
        payload = _query_runs(session, job, limit)
    return _normalize_payload(payload, session, job, limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    rows_affected INTEGER,
                    detail TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, started_at, finished_at, rows_affected, detail)
                VALUES
                    (1, 'daily', 'ok', '2025-01-01T00:00:00', '2025-01-01T00:00:02.500000', 10, '{}'),
                    (2, 'daily', 'failed', '2025-01-02T00:00:00', '2025-01-02T00:00:01', 0, '{}'),
                    (3, 'weekly', 'running', '2025-01-03T00:00:00', NULL, NULL, '{}')
                """
            )
        )

    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/runs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["by_status"]["failed"] == 1
        assert payload["summary"]["total"] == 3
        assert any(run["duration_seconds"] == 2.5 for run in payload["runs"])

    print("PASS")