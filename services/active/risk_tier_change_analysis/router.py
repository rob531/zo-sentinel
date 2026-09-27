"""router.py -- HTTP surface for risk_tier_change_analysis."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_tier_change, get_all_tier_changes

router = APIRouter(prefix="/api", tags=["risk_tier_change_analysis"])


class TierChangeItem(BaseModel):
    server_id: str
    current_tier: Optional[str]
    previous_tier: Optional[str]
    changed: bool
    change_direction: Optional[str]
    historical_record_count: Optional[int] = None


class TierChangeSummary(BaseModel):
    total_servers: int
    changed_count: int
    escalated_count: int
    de_escalated_count: int
    changes: List[TierChangeItem]


@router.get("/risk-tier-changes", response_model=TierChangeSummary)
def get_risk_tier_changes(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TierChangeSummary:
    """
    Return a summary of servers whose risk tier has changed within the lookback window.
    """
    all_changes = get_all_tier_changes(db, days_back=days)

    escalated = sum(1 for c in all_changes if c.get("change_direction") == "escalated")
    de_escalated = sum(1 for c in all_changes if c.get("change_direction") == "de_escalated")

    return TierChangeSummary(
        total_servers=len(all_changes),
        changed_count=escalated + de_escalated,
        escalated_count=escalated,
        de_escalated_count=de_escalated,
        changes=[TierChangeItem(**c) for c in all_changes],
    )


@router.get("/risk-tier-changes/{server_id}", response_model=TierChangeItem)
def get_server_tier_change(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TierChangeItem:
    """Return the tier change details for a specific server."""
    change = compute_tier_change(db, server_id, days_back=days)
    if change is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return TierChangeItem(**change)
