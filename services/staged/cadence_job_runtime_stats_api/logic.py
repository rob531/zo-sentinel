"""Logic and read-only API for cadence job runtime statistics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


def _clamp_days(days: int) -> int:
    return max(1, min(90, int(days)))


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(row: CadenceJobRun) -> Optional[float]:
    started_at = _as_utc(row.started_at)
    finished_at = _as_utc(row.finished_at)
    if started_at is None or finished_at is None:
        return None
    return max(0.0, (finished_at - started_at).total_seconds())


def _percentile_95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    # Nearest-rank percentile: ceil(0.95 * n) - 1.
    rank = max(1, (95 * len(ordered) + 99) // 100)
    return ordered[rank - 1]


def read_job_runtime_stats(session: Session, days: int) -> Dict[str, Any]:
    """Return runtime statistics for cadence jobs over the requested window."""
    window_days = _clamp_days(days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = session.execute(
        select(CadenceJobRun).where(
            CadenceJobRun.started_at.is_not(None),
            CadenceJobRun.started_at >= cutoff,
        )
    ).scalars().all()

    grouped: Dict[str, List[CadenceJobRun]] = {}
    for row in rows:
        grouped.setdefault(row.job or "unknown", []).append(row)

    jobs: List[Dict[str, Any]] = []
    for job, job_rows in grouped.items():
        completed_durations: List[float] = []
        ok_runs = 0
        failed_runs = 0
        incomplete_runs = 0
        total_rows_affected = 0

        for row in job_rows:
            duration = _duration_seconds(row)
            if duration is None:
                incomplete_runs += 1
            else:
                completed_durations.append(duration)

            status = str(row.status or "").strip().lower()
            if status in {"ok", "success", "succeeded", "completed", "complete", "passed"}:
                ok_runs += 1
            elif status in {"failed", "failure", "error", "errored"}:
                failed_runs += 1

            if row.rows_affected is not None:
                total_rows_affected += int(row.rows_affected)

        latest = max(
            job_rows,
            key=lambda row: _as_utc(row.started_at) or datetime.min.replace(tzinfo=timezone.utc),
        )
        runs = len(job_rows)
        failure_rate = failed_runs / runs if runs else 0.0

        jobs.append(
            {
                "job": job,
                "runs": runs,
                "ok_runs": ok_runs,
                "failed_runs": failed_runs,
                "incomplete_runs": incomplete_runs,
                "failure_rate": failure_rate,
                "avg_duration_seconds": (
                    sum(completed_durations) / len(completed_durations)
                    if completed_durations
                    else None
                ),
                "p95_duration_seconds": _percentile_95(completed_durations),
                "total_rows_affected": total_rows_affected,
                "last_status": latest.status,
                "last_started_at": latest.started_at,
            }
        )

    jobs.sort(key=lambda item: item["job"])
    return {
        "days": window_days,
        "jobs": jobs,
        "generated_at": datetime.now(timezone.utc),
    }


@router.get("/runtime-stats")
def get_runtime_stats(
    days: int = Query(7),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return read_job_runtime_stats(session, days)


@router.get("/{job}/runtime-stats")
def get_job_runtime_stats(
    job: str,
    days: int = Query(7),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    result = read_job_runtime_stats(session, days)
    result["jobs"] = [item for item in result["jobs"] if item["job"] == job]
    return result