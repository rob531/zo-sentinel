"""HTTP surface for cadence job duration profiling."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import cadence_job_duration_profile

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class JobDuration(BaseModel):
    job: str
    runs: int
    min_seconds: float
    median_seconds: float
    max_seconds: float
    rows_affected: int


class JobDurationProfileResponse(BaseModel):
    jobs: list[JobDuration]
    generated_at: datetime


@router.get("/job-duration", response_model=JobDurationProfileResponse)
def get_job_duration_profile(
    db: Session = Depends(get_session),
) -> JobDurationProfileResponse:
    profile: Any = cadence_job_duration_profile(db)
    jobs = profile.get("jobs", []) if isinstance(profile, dict) else profile
    generated_at = datetime.now(timezone.utc)
    if isinstance(profile, dict) and profile.get("generated_at") is not None:
        generated_at = profile["generated_at"]
    return JobDurationProfileResponse(jobs=jobs, generated_at=generated_at)