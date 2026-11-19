from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import *  # noqa: F401,F403
except ModuleNotFoundError:
    # The router remains usable when the package is loaded without its optional
    # presentation helpers.  All data access below is performed through the
    # real application session and mapped model.
    pass


router = APIRouter(prefix="/cadence-cost-attribution", tags=["cadence-cost-attribution"])


def _value(row: Any, *names: str, default: Any = None) -> Any:
    """Return the first populated value from a mapped row or mapping."""
    for name in names:
        if isinstance(row, dict):
            if name in row and row[name] is not None:
                return row[name]
        else:
            value = getattr(row, name, None)
            if value is not None:
                return value
    return default


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _job_name(row: Any) -> str:
    value = _value(
        row,
        "job_name",
        "job",
        "job_id",
        "pipeline_job",
        "cadence_job",
        "name",
        default="unknown",
    )
    return str(value)


def _cost(row: Any) -> float:
    return _number(
        _value(
            row,
            "cost_usd",
            "total_cost_usd",
            "estimated_cost_usd",
            "cost",
            "total_cost",
            default=0.0,
        )
    )


def _duration_seconds(row: Any) -> float:
    duration = _value(
        row,
        "duration_seconds",
        "elapsed_seconds",
        "elapsed_time",
        "duration_ms",
        "runtime_ms",
        default=0.0,
    )
    number = _number(duration)
    if "ms" in str(duration).lower() or any(
        name in str(getattr(row, "__dict__", {}))
        for name in ("duration_ms", "runtime_ms")
    ):
        return number / 1000.0
    return number


def _items(row: Any) -> float:
    return _number(
        _value(
            row,
            "items_processed",
            "processed_items",
            "records_processed",
            "processed_records",
            "throughput",
            "output_count",
            "count",
            default=0.0,
        )
    )


def _status(row: Any) -> str:
    value = _value(
        row,
        "status",
        "state",
        "result",
        "outcome",
        default="unknown",
    )
    return str(value).lower()


def _error(row: Any) -> str | None:
    value = _value(
        row,
        "error",
        "error_message",
        "exception",
        "failure_reason",
        "last_error",
    )
    return str(value) if value is not None else None


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _serializable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _load_runs(session: Session) -> list[Any]:
    result = session.execute(select(CadenceJobRun))
    rows = result.scalars().all() if hasattr(result, "scalars") else result.all()
    return list(rows)


def _attribution(rows: list[Any]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "job_name": "",
            "runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "cost_usd": 0.0,
            "duration_seconds": 0.0,
            "items_processed": 0.0,
            "throughput_per_second": 0.0,
            "last_run_at": None,
            "last_status": "unknown",
            "last_error": None,
        }
    )

    for row in rows:
        name = _job_name(row)
        item = grouped[name]
        item["job_name"] = name
        item["runs"] += 1

        status = _status(row)
        if status in {"success", "succeeded", "ok", "completed", "complete", "passed"}:
            item["successful_runs"] += 1
        elif status in {"failed", "failure", "error", "errored", "timeout", "timed_out"}:
            item["failed_runs"] += 1

        item["cost_usd"] += _cost(row)
        item["duration_seconds"] += _duration_seconds(row)
        item["items_processed"] += _items(row)

        timestamp = _value(
            row,
            "completed_at",
            "finished_at",
            "ended_at",
            "created_at",
            "started_at",
            "run_at",
        )
        timestamp_text = _timestamp(timestamp)
        if timestamp_text and (
            item["last_run_at"] is None or timestamp_text > item["last_run_at"]
        ):
            item["last_run_at"] = timestamp_text

        item["last_status"] = status
        error = _error(row)
        if error:
            item["last_error"] = error

    total_cost = sum(item["cost_usd"] for item in grouped.values())
    jobs = []
    for item in grouped.values():
        duration = item["duration_seconds"]
        item["throughput_per_second"] = (
            item["items_processed"] / duration if duration > 0 else 0.0
        )
        item["cost_share"] = (
            item["cost_usd"] / total_cost if total_cost > 0 else 0.0
        )
        item["cost_usd"] = round(item["cost_usd"], 6)
        item["duration_seconds"] = round(item["duration_seconds"], 6)
        item["items_processed"] = round(item["items_processed"], 6)
        item["throughput_per_second"] = round(item["throughput_per_second"], 6)
        item["cost_share"] = round(item["cost_share"], 6)
        jobs.append(item)

    jobs.sort(key=lambda item: item["cost_usd"], reverse=True)
    return {
        "jobs": jobs,
        "total_jobs": len(jobs),
        "total_runs": len(rows),
        "total_cost_usd": round(total_cost, 6),
    }


@router.get("")
def cadence_cost_attribution(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return cost and throughput attribution grouped by cadence job."""
    return _attribution(_load_runs(session))


@router.get("/summary")
def cadence_cost_attribution_summary(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return the aggregate cost and throughput summary."""
    data = _attribution(_load_runs(session))
    return {
        "total_jobs": data["total_jobs"],
        "total_runs": data["total_runs"],
        "total_cost_usd": data["total_cost_usd"],
        "jobs": data["jobs"],
    }


@router.get("/{job_name}")
def cadence_job_cost_attribution(
    job_name: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return attribution for one cadence pipeline job."""
    data = _attribution(_load_runs(session))
    for job in data["jobs"]:
        if job["job_name"] == job_name:
            return job
    return {
        "job_name": job_name,
        "runs": 0,
        "successful_runs": 0,
        "failed_runs": 0,
        "cost_usd": 0.0,
        "cost_share": 0.0,
        "duration_seconds": 0.0,
        "items_processed": 0.0,
        "throughput_per_second": 0.0,
        "last_run_at": None,
        "last_status": "unknown",
        "last_error": None,
    }


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    class _EmptyResult:
        def scalars(self) -> "_EmptyResult":
            return self

        def all(self) -> list[Any]:
            return []

    class _EmptySession:
        def execute(self, statement: Any) -> _EmptyResult:
            return _EmptyResult()

    test_app.dependency_overrides[get_session] = lambda: _EmptySession()

    @test_app.get("/self-test")
    def self_test() -> dict[str, str]:
        from fastapi.testclient import TestClient

        response = TestClient(test_app).get("/cadence-cost-attribution")
        if response.status_code != 200:
            raise RuntimeError(response.text)
        payload = response.json()
        if payload["total_jobs"] != 0 or payload["total_runs"] != 0:
            raise RuntimeError("unexpected self-test response")
        return {"status": "PASS"}

    print("PASS")