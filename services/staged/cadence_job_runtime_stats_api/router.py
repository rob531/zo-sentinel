"""HTTP surface for cadence job runtime statistics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import read_job_runtime_stats

router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


@router.get("/runtime-stats")
def get_runtime_stats(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> dict:
    return read_job_runtime_stats(db, days)


@router.get("/{job}/runtime-stats")
def get_job_runtime_stats(
    job: str,
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> dict:
    result = read_job_runtime_stats(db, days)
    result["jobs"] = [
        job_stats for job_stats in result.get("jobs", []) if job_stats.get("job") == job
    ]
    return result