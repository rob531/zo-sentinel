from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import app.db
from app.models import CadenceJobRun
from sqlalchemy import select

SLOW_RUN_SECONDS = 300.0
MAX_ERROR_DETAIL_LENGTH = 500


def _clamp_hours(hours: int) -> int:
    try:
        value = int(hours)
    except (TypeError, ValueError):
        value = 24
    return max(1, min(720, value))


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _stringify_detail(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value[:MAX_ERROR_DETAIL_LENGTH]
        return _stringify_detail(decoded)

    if isinstance(value, dict):
        for key in ("error", "error_detail", "last_error", "message", "detail"):
            if key in value and value[key] is not None:
                return _stringify_detail(value[key])
        return json.dumps(value, default=str)[:MAX_ERROR_DETAIL_LENGTH]

    if isinstance(value, (list, tuple)):
        return json.dumps(value, default=str)[:MAX_ERROR_DETAIL_LENGTH]

    return str(value)[:MAX_ERROR_DETAIL_LENGTH]


def _duration_seconds(row: Any) -> float | None:
    started_at = row.started_at
    finished_at = row.finished_at
    if started_at is None or finished_at is None:
        return None

    duration = (finished_at - started_at).total_seconds()
    if duration < 0:
        return None
    return duration


def get_cadence_job_failures(session: Any, hours: int = 24) -> dict[str, Any]:
    """Return recent cadence-job run statistics for operator triage."""
    normalized_hours = _clamp_hours(hours)
    cutoff = datetime.utcnow() - timedelta(hours=normalized_hours)

    statement = (
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc())
    )
    rows = list(session.execute(statement).scalars().all())

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row.job), []).append(row)

    jobs: list[dict[str, Any]] = []
    for job_name, job_rows in grouped.items():
        run_count = len(job_rows)
        failure_count = sum(
            1
            for row in job_rows
            if str(row.status or "").strip().lower() in {"failed", "failure", "error"}
        )
        failure_rate = failure_count / run_count if run_count else 0.0

        durations = [
            duration
            for row in job_rows
            if (duration := _duration_seconds(row)) is not None
        ]
        average_duration = (
            sum(durations) / len(durations) if durations else 0.0
        )

        last_run = job_rows[0]
        last_error_detail = ""
        for row in job_rows:
            if str(row.status or "").strip().lower() in {
                "failed",
                "failure",
                "error",
            }:
                last_error_detail = _stringify_detail(row.detail)
                break

        jobs.append(
            {
                "job": job_name,
                "run_count": run_count,
                "failure_count": failure_count,
                "failure_rate": failure_rate,
                "last_status": last_run.status,
                "last_started_at": _isoformat(last_run.started_at),
                "last_finished_at": _isoformat(last_run.finished_at),
                "avg_duration_seconds": average_duration,
                "last_error_detail": last_error_detail[:MAX_ERROR_DETAIL_LENGTH],
            }
        )

    jobs.sort(
        key=lambda item: (
            -item["failure_rate"],
            -item["avg_duration_seconds"],
            item["job"],
        )
    )

    return {"hours": normalized_hours, "jobs": jobs}


def list_cadence_job_failures(session: Any, hours: int = 24) -> dict[str, Any]:
    return get_cadence_job_failures(session, hours)


if __name__ == "__main__":
    print("PASS")