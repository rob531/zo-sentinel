"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service's logic module.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cadence_run_history

router = APIRouter(prefix="/api", tags=["cadence"])


class RunItem(BaseModel):
    id: int
    job: str
    status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    rows_affected: int | None = None


class CadenceRunDurationHistoryResponse(BaseModel):
    runs: list[RunItem]
    by_status: dict[str, int]
    job_count: int


@router.get(
    "/cadence/runs",
    response_model=CadenceRunDurationHistoryResponse,
    summary="Retrieve recent Cadence job runs",
)
def get_cadence_runs(
    job: Optional[str] = Query(
        default=None,
        description="Filter runs by job name",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of runs to return (1‑500)",
    ),
    db: Session = Depends(get_session),
) -> CadenceRunDurationHistoryResponse:
    """Return recent CadenceJobRun records with aggregated status counts.

    The `limit` is clamped to the range 1‑500; `job` filters by the `job` column
    when provided.
    """
    # Ensure limit respects the hard bounds even if FastAPI validation is bypassed.
    limit = max(1, min(limit, 500))

    result = get_cadence_run_history(db, job=job, limit=limit)

    # `result` is expected to be a dict compatible with the response model.
    return CadenceRunDurationHistoryResponse(**result)