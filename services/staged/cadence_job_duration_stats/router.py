"""HTTP surface for cadence job duration statistics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import cadence_job_duration_stats

router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


class JobDurationStats(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    mean_seconds: float
    median_seconds: float
    p95_seconds: float
    mean_rows_affected: float


class CadenceJobDurationStatsResponse(BaseModel):
    days: int
    jobs: list[JobDurationStats]


@router.get(
    "/duration",
    response_model=CadenceJobDurationStatsResponse,
)
def get_cadence_job_duration_stats(
    db: Session = Depends(get_session),
    days: int = Query(default=30, ge=1, le=3650),
) -> CadenceJobDurationStatsResponse:
    result = cadence_job_duration_stats(db, days=days)
    return CadenceJobDurationStatsResponse.model_validate(result)