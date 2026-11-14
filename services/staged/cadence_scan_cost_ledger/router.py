"""HTTP surface for the cadence scan cost ledger."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import cost_ledger

router = APIRouter(prefix="/api", tags=["cadence"])


class JobCost(BaseModel):
    job: str
    runs: int
    failures: int
    median_seconds: float
    rows_affected: int


class CostLedgerResponse(BaseModel):
    days: int
    jobs: list[JobCost]


@router.get("/cadence/cost", response_model=CostLedgerResponse)
def get_cost_ledger(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> CostLedgerResponse:
    result = cost_ledger(db, days)
    return CostLedgerResponse(
        days=days,
        jobs=[JobCost(**job) for job in result["jobs"]],
    )