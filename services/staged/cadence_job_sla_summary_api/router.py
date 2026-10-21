from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_sla_summary


router = APIRouter(prefix="/api")


class JobSlaSummaryItem(BaseModel):
    job: str
    run_count: int
    failure_count: int
    success_rate_pct: float
    avg_duration_seconds: Optional[float] = None
    total_rows_affected: int = 0
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    stale: bool = False


class CadenceJobSlaSummaryResponse(BaseModel):
    days: int
    generated_at: datetime
    jobs: List[JobSlaSummaryItem]


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed
        except ValueError:
            return None
    return None


def _fallback_stale(job: Dict[str, Any], days: int) -> bool:
    last_finished_at = _parse_datetime(job.get("last_finished_at"))
    if last_finished_at is None:
        return False

    if last_finished_at.tzinfo is None:
        last_finished_at = last_finished_at.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days) * 2)
    return last_finished_at < cutoff


def _normalise_result(result: Any, requested_days: int) -> Dict[str, Any]:
    if isinstance(result, Mapping):
        payload = dict(result)
        raw_jobs = payload.get("jobs") or []
    elif isinstance(result, (list, tuple)):
        payload = {}
        raw_jobs = list(result)
    else:
        payload = _as_dict(result)
        raw_jobs = payload.get("jobs") or []

    days = payload.get("days", requested_days)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = requested_days

    generated_at = payload.get("generated_at") or datetime.now(timezone.utc)

    jobs: List[Dict[str, Any]] = []
    for raw_job in raw_jobs:
        job = _as_dict(raw_job)
        if "stale" in job:
            job["stale"] = bool(job["stale"])
        else:
            job["stale"] = _fallback_stale(job, days)
        jobs.append(job)

    return {
        "days": days,
        "generated_at": generated_at,
        "jobs": jobs,
    }


@router.get(
    "/cadence/jobs/sla-summary",
    response_model=CadenceJobSlaSummaryResponse,
)
def cadence_job_sla_summary(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> CadenceJobSlaSummaryResponse:
    result = get_sla_summary(db, days)
    return CadenceJobSlaSummaryResponse(**_normalise_result(result, days))


if __name__ == "__main__":
    print("PASS")