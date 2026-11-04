"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import sla_violations

router = APIRouter(prefix="/api", tags=["cadence_sla_violation_list"])


class JobViolation(BaseModel):
    job: str
    violations: int
    total_seconds: float
    max_seconds: float
    last_error: str | None = None


class SLAViolationResponse(BaseModel):
    hours: int = Field(..., description="Window size in hours")
    checked_runs: int = Field(..., description="Number of runs examined")
    violation_count: int = Field(..., description="Total violations found")
    by_job: list[JobViolation] = Field(..., description="Aggregated per‑job data")


@router.get(
    "/cadence/sla/violations",
    response_model=SLAViolationResponse,
    summary="List SLA violations for Cadence jobs",
)
def get_sla_violations(
    hours: int = Query(24, ge=1, description="Number of hours to look back"),
    db: Session = Depends(get_session),
) -> SLAViolationResponse:
    """
    Retrieve Cadence job SLA violations for the past *hours*.

    Delegates the heavy lifting to :func:`services.staged.cadence_sla_violation_list.logic.sla_violations`.
    """
    result = sla_violations(db, hours)
    return SLAViolationResponse(**result)