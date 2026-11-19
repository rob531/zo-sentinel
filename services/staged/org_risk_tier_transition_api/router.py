"""router.py -- the service's HTTP surface (one concern, one file).

Thin wrapper that injects the real Session via Depends(get_session) and
delegates to the service's logic module.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic function returns a dict compatible with the response model.
from .logic import get_org_risk_tier_transitions

router = APIRouter(prefix="/api", tags=["org_risk_tier_transition"])


class Transition(BaseModel):
    server_id: str
    server_name: str
    old_tier: str | None = None
    new_tier: str
    change_type: str
    moved: bool
    occurred_at: datetime = Field(..., alias="occurred_at")


class Counts(BaseModel):
    moved: int
    unchanged: int
    critical_to_safe: int
    safe_to_critical: int


class TierTransitionsResponse(BaseModel):
    org_id: str
    days: int
    transitions: List[Transition]
    counts: Counts
    verdict_counts: Dict[str, int]


@router.get(
    "/orgs/tier-transitions",
    response_model=TierTransitionsResponse,
    summary="Get tier transition history for an organization",
)
def get_tier_transitions(
    org_id: str,
    days: int = 30,
    db: Session = Depends(get_session),
) -> TierTransitionsResponse:
    """
    Retrieve per‑server tier movement for the given organization over the
    past *days* (default 30). The heavy lifting is performed by
    ``services.staged.org_risk_tier_transition_api.logic.get_org_risk_tier_transitions``.
    """
    result = get_org_risk_tier_transitions(db, org_id, days)
    return TierTransitionsResponse(**result)