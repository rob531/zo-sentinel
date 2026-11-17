"""HTTP surface for the cadence job run ledger."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import list_cadence_runs

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class RunEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_affected: int | None = None
    detail: str | None = None


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    failed: int
    last_finished_at: str | None = None


class RunLedgerResponse(BaseModel):
    runs: list[RunEntry]
    total: int
    by_status: dict[str, int]
    by_job: dict[str, JobSummary]


@router.get("/runs", response_model=RunLedgerResponse)
def get_runs(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> RunLedgerResponse:
    return RunLedgerResponse.model_validate(list_cadence_runs(db, limit=limit))