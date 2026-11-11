from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceJobScheduleResponse(BaseModel):
    job: str
    days: int
    run_count: int
    success_count: int
    failure_count: int
    total_rows_affected: int
    median_run_seconds: float
    p95_run_seconds: float
    last_run_at: Optional[datetime]
    last_status: Optional[str]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])

    fraction = position - lower
    return float(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    )


def _is_success(status: Optional[str]) -> bool:
    return (status or "").strip().lower() in {
        "success",
        "succeeded",
        "ok",
        "complete",
        "completed",
    }


def _is_failure(status: Optional[str]) -> bool:
    return (status or "").strip().lower() in {
        "failure",
        "failed",
        "fail",
        "error",
        "errored",
    }


@router.get(
    "/cadence/jobs/{job}/schedule",
    response_model=CadenceJobScheduleResponse,
)
def get_cadence_job_schedule(
    job: str,
    days: int = Query(default=14, ge=1, le=90),
    session=Depends(get_session),
) -> CadenceJobScheduleResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    runs = (
        session.query(CadenceJobRun)
        .filter(
            CadenceJobRun.job == job,
            CadenceJobRun.started_at >= cutoff,
        )
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
        .all()
    )

    durations: list[float] = []
    for run in runs:
        if run.finished_at is None:
            continue
        seconds = (
            _as_utc(run.finished_at) - _as_utc(run.started_at)
        ).total_seconds()
        durations.append(max(0.0, float(seconds)))

    success_count = sum(1 for run in runs if _is_success(run.status))
    failure_count = sum(1 for run in runs if _is_failure(run.status))
    total_rows_affected = sum(int(run.rows_affected or 0) for run in runs)
    last_run = runs[0] if runs else None

    return CadenceJobScheduleResponse(
        job=job,
        days=days,
        run_count=len(runs),
        success_count=success_count,
        failure_count=failure_count,
        total_rows_affected=total_rows_affected,
        median_run_seconds=_percentile(durations, 0.50),
        p95_run_seconds=_percentile(durations, 0.95),
        last_run_at=last_run.started_at if last_run is not None else None,
        last_status=last_run.status if last_run is not None else None,
    )


get_job_schedule = get_cadence_job_schedule
JobScheduleResponse = CadenceJobScheduleResponse

app = FastAPI(title="cadence_job_schedule_detail_api")
app.include_router(router)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine)
    test_session = TestingSession()

    now = datetime.now(timezone.utc)
    expected_total = 0
    for index in range(6):
        rows_affected = index + 1
        expected_total += rows_affected
        test_session.add(
            CadenceJobRun(
                job="mcp_scanner",
                status="success" if index < 4 else "failure",
                started_at=now - timedelta(days=index % 2, hours=index),
                finished_at=now
                - timedelta(days=index % 2, hours=index)
                + timedelta(seconds=10 + index),
                rows_affected=rows_affected,
                detail=f"seed-{index}",
            )
        )
    test_session.commit()

    that_app = FastAPI()

    def override_get_session():
        yield test_session

    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    async def run_request() -> tuple[int, dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        path = "/api/cadence/jobs/mcp_scanner/schedule?days=14"

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path.split("?", 1)[0],
            "raw_path": path.split("?", 1)[0].encode(),
            "query_string": b"days=14",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        await that_app(scope, receive, send)
        status = next(
            message["status"]
            for message in messages
            if message["type"] == "http.response.start"
        )
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return status, json.loads(body.decode())

    status, payload = asyncio.run(run_request())
    assert status == 200
    assert payload["run_count"] == 6
    assert payload["failure_count"] == 2
    assert payload["total_rows_affected"] == expected_total
    assert isinstance(payload["median_run_seconds"], float)
    assert payload["median_run_seconds"] >= 0
    print("PASS")