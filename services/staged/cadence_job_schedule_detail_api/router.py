"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cadence_job_schedule_detail

router = APIRouter(prefix="/api", tags=["cadence_job_schedule_detail"])


class ScheduleResponse(BaseModel):
    job: str
    days: int
    run_count: int
    success_count: int
    failure_count: int
    total_rows_affected: int
    median_run_seconds: float
    p95_run_seconds: float
    last_run_at: str | None
    last_status: str | None


@router.get(
    "/cadence/jobs/{job}/schedule",
    response_model=ScheduleResponse,
)
def get_schedule(
    job: str,
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_session),
) -> ScheduleResponse:
    """Return schedule statistics for a Cadence job over the past *days*."""
    data = get_cadence_job_schedule_detail(db, job, days)
    return ScheduleResponse(**data)