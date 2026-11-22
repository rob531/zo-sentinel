"""router.py -- HTTP surface for the cadence job timeline API.

Provides a thin FastAPI router that injects a real DB session and
delegates all business logic to ``services.staged.app3t_cadence_job_timeline_api.logic``.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cadence_job_timeline


router = APIRouter(prefix="/api", tags=["cadence_job_timeline"])


class JobEntry(BaseModel):
    job: str
    status: str
    runs: int
    rows_affected: int
    last_started_at: datetime | None = None


class TimelineEntry(BaseModel):
    date: date
    job: str
    status: str
    runs: int
    rows_affected: int


class TimelineResponse(BaseModel):
    days: int
    jobs: List[JobEntry]
    timeline: List[TimelineEntry]


@router.get(
    "/cadence/jobs/timeline",
    response_model=TimelineResponse,
    summary="Timeline of cadence job runs over the past N days",
)
def get_timeline(
    days: int = Query(..., ge=1, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> TimelineResponse:
    """Return aggregated cadence job run information for the last ``days`` days."""
    result = get_cadence_job_timeline(db, days)
    return TimelineResponse(**result)