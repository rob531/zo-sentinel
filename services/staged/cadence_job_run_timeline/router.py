"""HTTP surface for cadence job run timelines."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cadence_timeline

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class JobRun(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float
    rows_affected: int | None = None


class JobTimeline(BaseModel):
    job: str
    runs: list[JobRun]
    success_count: int
    failure_count: int


class CadenceTimelineResponse(BaseModel):
    hours: int
    jobs: list[JobTimeline]


@router.get("/runs", response_model=CadenceTimelineResponse)
def get_runs(
    job: str | None = None,
    hours: int = 24,
    db: Session = Depends(get_session),
) -> CadenceTimelineResponse:
    timeline = get_cadence_timeline(db, job, hours)
    jobs = timeline.get("jobs", []) if isinstance(timeline, dict) else timeline
    return CadenceTimelineResponse(hours=hours, jobs=jobs)