from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {
    "ok",
    "success",
    "successful",
    "succeeded",
    "complete",
    "completed",
    "done",
}


def bucket_status(status: str) -> str:
    """Map a run status to the two operator-facing outcome buckets."""
    try:
        normalised = str(status or "").strip().lower()
    except Exception:
        normalised = ""

    return "ok" if normalised in _SUCCESS_STATUSES else "failed"


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _isoformat(value: Any) -> str | None:
    parsed = _as_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone(timezone.utc).isoformat()


def _run_time(run: dict[str, Any]) -> datetime | None:
    return _as_datetime(run.get("started_at")) or _as_datetime(run.get("finished_at"))


def _duration_seconds(run: dict[str, Any]) -> float | None:
    started_at = _as_datetime(run.get("started_at"))
    finished_at = _as_datetime(run.get("finished_at"))
    if started_at is None or finished_at is None:
        return None

    if started_at.tzinfo is None and finished_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=finished_at.tzinfo)
    elif finished_at.tzinfo is None and started_at.tzinfo is not None:
        finished_at = finished_at.replace(tzinfo=started_at.tzinfo)

    duration = (finished_at - started_at).total_seconds()
    return max(0.0, float(duration))


def summarise_job(runs: list[dict]) -> dict:
    """Summarise runs for one job without raising on missing values."""
    safe_runs = [run for run in (runs or []) if isinstance(run, dict)]
    if not safe_runs:
        return {
            "job": "unknown",
            "runs": 0,
            "ok": 0,
            "failed": 0,
            "success_rate": 0.0,
            "avg_duration_seconds": 0.0,
            "last_run_at": None,
            "last_status": "failed",
        }

    first_job = safe_runs[0].get("job")
    job = str(first_job) if first_job is not None else "unknown"

    statuses = [bucket_status(run.get("status")) for run in safe_runs]
    ok_count = sum(status == "ok" for status in statuses)
    failed_count = len(statuses) - ok_count
    durations = [
        duration
        for run in safe_runs
        if (duration := _duration_seconds(run)) is not None
    ]

    dated_runs = [
        (run_time, index, run)
        for index, run in enumerate(safe_runs)
        if (run_time := _run_time(run)) is not None
    ]
    if dated_runs:
        last_run_at, _, last_run = max(
            dated_runs,
            key=lambda item: (_utc_naive(item[0]), item[1]),
        )
        last_status = bucket_status(last_run.get("status"))
        last_run_iso = _isoformat(last_run_at)
    else:
        last_status = bucket_status(safe_runs[-1].get("status"))
        last_run_iso = None

    run_count = len(safe_runs)
    return {
        "job": job,
        "runs": run_count,
        "ok": ok_count,
        "failed": failed_count,
        "success_rate": round(ok_count / run_count, 3) if run_count else 0.0,
        "avg_duration_seconds": round(sum(durations) / len(durations), 3)
        if durations
        else 0.0,
        "last_run_at": last_run_iso,
        "last_status": last_status,
    }


async def get_failure_buckets(session: Any, window_days: int = 7) -> dict:
    """Read recent cadence job runs and return operator-facing failure buckets."""
    try:
        days = max(1, int(window_days))
    except (TypeError, ValueError):
        days = 7

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(CadenceJobRun).where(
        or_(
            CadenceJobRun.started_at.is_(None),
            CadenceJobRun.started_at >= cutoff,
        )
    )

    result = session.execute(statement)
    if inspect.isawaitable(result):
        result = await result
    rows = result.scalars().all()

    runs: list[dict[str, Any]] = []
    for row in rows:
        runs.append(
            {
                "id": getattr(row, "id", None),
                "job": getattr(row, "job", None),
                "status": getattr(row, "status", None),
                "started_at": getattr(row, "started_at", None),
                "finished_at": getattr(row, "finished_at", None),
                "rows_affected": getattr(row, "rows_affected", None),
                "detail": getattr(row, "detail", None),
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        job = run.get("job")
        job_name = str(job) if job is not None else "unknown"
        grouped.setdefault(job_name, []).append(run)

    jobs = [summarise_job(job_runs) for job_runs in grouped.values()]
    jobs.sort(
        key=lambda item: (
            -int(item.get("failed", 0)),
            float(item.get("success_rate", 0.0)),
            -float(item.get("avg_duration_seconds", 0.0)),
            str(item.get("job", "")),
        )
    )

    total_runs = sum(int(job.get("runs", 0)) for job in jobs)
    worst_job = jobs[0].get("job") if jobs else None

    return {
        "jobs": jobs,
        "total_runs": total_runs,
        "window_days": days,
        "worst_job": worst_job,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


get_cadence_job_failure_buckets = get_failure_buckets
get_job_failure_buckets = get_failure_buckets


if __name__ == "__main__":
    test_runs = [
        {
            "job": "risk_ranker",
            "status": "ok",
            "started_at": "2024-01-01T00:00:00Z",
            "finished_at": "2024-01-01T00:00:02Z",
        },
        {
            "job": "risk_ranker",
            "status": "ok",
            "started_at": "2024-01-01T00:01:00Z",
            "finished_at": "2024-01-01T00:01:03Z",
        },
        {
            "job": "risk_ranker",
            "status": "ok",
            "started_at": "2024-01-01T00:02:00Z",
            "finished_at": "2024-01-01T00:02:04Z",
        },
        {
            "job": "risk_ranker",
            "status": "error",
            "started_at": "2024-01-01T00:03:00Z",
            "finished_at": "2024-01-01T00:02:00Z",
        },
    ]
    summary = summarise_job(test_runs)
    assert summary["runs"] == 4
    assert summary["failed"] == 1
    assert summary["success_rate"] == 0.75
    assert summary["avg_duration_seconds"] >= 0
    print("PASS")