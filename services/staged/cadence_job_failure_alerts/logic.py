"""logic.py -- cadence job failure summary computation over the real app data layer."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {"success", "ok", "succeeded"}


def summarize_runs(rows: list[dict]) -> list[dict]:
    """Aggregate cadence job runs and return the top 50 jobs by failures."""
    summaries: Dict[str, dict] = {}

    for row in rows:
        job = row.get("job") or "unknown"
        status = row.get("status") or ""
        is_success = status.lower() in _SUCCESS_STATUSES

        started_at = row.get("started_at")
        finished_at = row.get("finished_at")
        duration_seconds = None
        if started_at is not None and finished_at is not None:
            duration_seconds = (finished_at - started_at).total_seconds()

        rows_affected = row.get("rows_affected") or 0
        try:
            rows_affected = int(rows_affected)
        except (TypeError, ValueError):
            rows_affected = 0

        if job not in summaries:
            summaries[job] = {
                "job": job,
                "total_runs": 0,
                "failed_runs": 0,
                "success_rate": 0.0,
                "last_failure_at": None,
                "avg_duration_seconds": 0.0,
                "total_rows_affected": 0,
                "_duration_total": 0.0,
                "_duration_count": 0,
            }

        summary = summaries[job]
        summary["total_runs"] += 1
        summary["total_rows_affected"] += rows_affected

        if is_success:
            pass
        else:
            summary["failed_runs"] += 1
            if (
                summary["last_failure_at"] is None
                or started_at is not None
                and started_at > summary["last_failure_at"]
            ):
                summary["last_failure_at"] = started_at

        if duration_seconds is not None:
            summary["_duration_total"] += duration_seconds
            summary["_duration_count"] += 1

    output: List[dict] = []
    for summary in summaries.values():
        total_runs = summary["total_runs"]
        failed_runs = summary["failed_runs"]
        successful_runs = total_runs - failed_runs
        summary["success_rate"] = (
            float(successful_runs) / total_runs if total_runs else 0.0
        )
        summary["avg_duration_seconds"] = (
            summary["_duration_total"] / summary["_duration_count"]
            if summary["_duration_count"]
            else 0.0
        )
        summary.pop("_duration_total")
        summary.pop("_duration_count")
        output.append(summary)

    output.sort(key=lambda item: (-item["failed_runs"], item["job"]))
    return output[:50]


def get_cadence_job_failures(db: Session, hours: int = 24) -> dict:
    """Read recent cadence runs and return failures grouped by job name."""
    try:
        hours = int(hours)
    except (TypeError, ValueError) as exc:
        raise ValueError("hours must be an integer") from exc
    if hours < 0:
        raise ValueError("hours must not be negative")

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    result = db.execute(
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
        ).where(CadenceJobRun.started_at >= cutoff)
    ).mappings().all()

    return {
        "hours": hours,
        "jobs": summarize_runs([dict(row) for row in result]),
    }


# Compatibility aliases for callers using the service-specific endpoint names.
get_failure_summary = get_cadence_job_failures
get_job_failures = get_cadence_job_failures
get_cadence_failure_summary = get_cadence_job_failures


if __name__ == "__main__":
    _rows = [
        {
            "job": "failed-job",
            "status": "error",
            "started_at": datetime(2024, 1, 1, 0, 0, 0),
            "finished_at": datetime(2024, 1, 1, 0, 0, 5),
            "rows_affected": 0,
        },
        {
            "job": "failed-job",
            "status": "failure",
            "started_at": datetime(2024, 1, 1, 1, 0, 0),
            "finished_at": datetime(2024, 1, 1, 1, 0, 7),
            "rows_affected": 0,
        },
        {
            "job": "successful-job",
            "status": "success",
            "started_at": datetime(2024, 1, 1, 2, 0, 0),
            "finished_at": datetime(2024, 1, 1, 2, 0, 3),
            "rows_affected": 4,
        },
        {
            "job": "successful-job",
            "status": "succeeded",
            "started_at": datetime(2024, 1, 1, 3, 0, 0),
            "finished_at": datetime(2024, 1, 1, 3, 0, 3),
            "rows_affected": 6,
        },
    ]
    _result = summarize_runs(_rows)
    assert len(_result) == 2
    assert _result[0]["job"] == "failed-job"
    assert _result[0]["failed_runs"] == 2
    assert _result[0]["success_rate"] == 0.0
    assert _result[1]["success_rate"] == 1.0
    print("PASS")