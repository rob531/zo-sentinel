"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for mounting, injects the real database session, and delegates
failure classification to the service logic.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import classify_job_failures

router = APIRouter(prefix="/api", tags=["cadence_job_failure_classification"])


class FailureSampleResponse(BaseModel):
    bucket: str
    detail_excerpt: str
    finished_at: datetime | None = None


class JobFailureClassResponse(BaseModel):
    job: str
    status: str
    failed_runs: int
    buckets: dict[str, int]
    samples: list[FailureSampleResponse]


class JobFailureClassesResponse(BaseModel):
    days: int
    jobs: list[JobFailureClassResponse]


@router.get(
    "/cadence/job-failure-classes",
    response_model=JobFailureClassesResponse,
)
def get_job_failure_classes(
    job: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> JobFailureClassesResponse:
    result = classify_job_failures(db, job=job, days=days)
    jobs = result.get("jobs", []) if isinstance(result, dict) else result
    return JobFailureClassesResponse(days=days, jobs=jobs)