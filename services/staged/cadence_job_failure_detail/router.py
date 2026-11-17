"""HTTP surface for cadence job failure drill-downs."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cadence_job_failures

router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


class CadenceJobFailure(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None
    detail: Optional[str] = None


class CadenceJobFailureAggregate(BaseModel):
    job: str
    failures: int
    last_failed_at: datetime
    last_status: str
    total_rows_affected: int


class CadenceJobFailuresResponse(BaseModel):
    days: int
    limit: int
    failures: list[CadenceJobFailure]
    by_job: list[CadenceJobFailureAggregate]


@router.get("/failures", response_model=CadenceJobFailuresResponse)
def get_failures(
    days: int = Query(default=7, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> CadenceJobFailuresResponse:
    result = get_cadence_job_failures(db, days=days, limit=limit)
    return CadenceJobFailuresResponse(
        days=days,
        limit=limit,
        failures=result["failures"],
        by_job=result["by_job"],
    )