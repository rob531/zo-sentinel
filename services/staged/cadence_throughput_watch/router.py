"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import cadence_throughput

router = APIRouter(prefix="/api", tags=["cadence_throughput_watch"])


class JobInfo(BaseModel):
    job: str
    runs: int
    ok: int
    failed: int
    rows_affected: int
    avg_duration_seconds: float
    last_status: str
    last_started_at: str | None
    last_finished_at: str | None


class ThroughputResponse(BaseModel):
    hours: int
    job: str | None
    jobs: list[JobInfo]
    total_runs: int
    failed_runs: int


@router.get(
    "/cadence/throughput",
    response_model=ThroughputResponse,
    summary="Live view of cadence job throughput",
)
def get_cadence_throughput(
    hours: int = 24,
    job: str | None = None,
    db: Session = Depends(get_session),
) -> ThroughputResponse:
    """Delegate to the business logic and return a typed response."""
    result = cadence_throughput(db, hours=hours, job=job)
    return ThroughputResponse(**result)