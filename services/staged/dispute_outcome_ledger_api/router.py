"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service's logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_outcome_ledger

router = APIRouter(prefix="/api", tags=["dispute_outcome_ledger"])


class RecentDispute(BaseModel):
    id: int
    server_id: int
    server_name: str | None
    proposed_overall_risk: str
    reason_category: str
    status: str
    created_at: str
    resolved_at: str | None


class DisputeOutcomeLedgerResponse(BaseModel):
    days: int
    by_status: dict[str, int]
    by_reason_category: dict[str, int]
    by_proposed_tier: dict[str, int]
    avg_resolution_hours: float | None
    recent: list[RecentDispute]


@router.get(
    "/disputes/outcomes",
    response_model=DisputeOutcomeLedgerResponse,
    summary="Get dispute outcome ledger",
)
def get_outcomes(
    days: int = Query(..., ge=0, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> DisputeOutcomeLedgerResponse:
    """Return aggregated dispute outcome data for the past ``days`` days."""
    ledger = get_dispute_outcome_ledger(db, days)
    return DisputeOutcomeLedgerResponse(**ledger)