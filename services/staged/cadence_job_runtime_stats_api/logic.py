"""Read-only runtime statistics for cadence job runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api", tags=["cadence"])


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(started_at: datetime, finished_at: datetime) -> float:
    start = _as_utc(started_at)
    finish = _as_utc(finished_at)
    if start is None or finish is None:
        return 0.0
    return max(0.0, (finish - start).total_seconds())


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, ceil(0.95 * len(ordered)) - 1)
    return float(ordered[index])


def read_job_runtime_stats(
    session: Session,
    days: int,
    job: Optional[str] = None,
) -> Dict[str, Any]:
    """Return aggregate execution statistics for recent cadence job runs."""
    days = max(1, min(90, int(days)))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    statement = select(
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
        CadenceJobRun.rows_affected,
    ).where(CadenceJobRun.started_at >= cutoff)

    rows = session.execute(statement).all()

    grouped: Dict[str, List[Any]] = {}
    for row in rows:
        if job is not None and row.job != job:
            continue
        grouped.setdefault(row.job, []).append(row)

    jobs: List[Dict[str, Any]] = []
    for job_name, job_rows in sorted(grouped.items()):
        runs = len(job_rows)
        ok_runs = 0
        failed_runs = 0
        incomplete_runs = 0
        durations: List[float] = []
        total_rows_affected = 0

        for row in job_rows:
            status = (row.status or "").strip().lower()
            if status in {"ok", "success", "succeeded"}:
                ok_runs += 1
            elif status in {"failed", "failure", "error"}:
                failed_runs += 1

            if row.finished_at is None:
                incomplete_runs += 1
            else:
                durations.append(_duration_seconds(row.started_at, row.finished_at))

            if row.rows_affected is not None:
                total_rows_affected += int(row.rows_affected)

        latest = max(
            job_rows,
            key=lambda row: _as_utc(row.started_at) or datetime.min.replace(tzinfo=timezone.utc),
        )
        latest_started = _as_utc(latest.started_at)

        jobs.append(
            {
                "job": job_name,
                "runs": runs,
                "ok_runs": ok_runs,
                "failed_runs": failed_runs,
                "incomplete_runs": incomplete_runs,
                "failure_rate": (failed_runs / runs) if runs else 0.0,
                "avg_duration_seconds": (
                    sum(durations) / len(durations) if durations else 0.0
                ),
                "p95_duration_seconds": _p95(durations),
                "total_rows_affected": total_rows_affected,
                "last_status": latest.status,
                "last_started_at": latest_started.isoformat() if latest_started else None,
            }
        )

    return {
        "days": days,
        "jobs": jobs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/cadence/jobs/runtime-stats")
def runtime_stats(
    days: int = Query(7, ge=1, le=90),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return read_job_runtime_stats(session, days)


@router.get("/cadence/jobs/{job}/runtime-stats")
def job_runtime_stats(
    job: str,
    days: int = Query(7, ge=1, le=90),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    result = read_job_runtime_stats(session, days, job=job)
    if not result["jobs"]:
        raise HTTPException(status_code=404, detail="Cadence job not found")
    return result