"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import triage_disputes

router = APIRouter(prefix="/api", tags=["score_dispute_triage"])


class DisputeItem(BaseModel):
    id: int
    server_id: int
    proposed_overall_risk: str
    current_risk_tier: str | None
    tier_shift: bool
    reason_category: str
    status: str
    created_at: str  # ISO‑8601 datetime string


class TriageResponse(BaseModel):
    total: int
    by_reason: dict[str, int]
    items: list[DisputeItem]


@router.get(
    "/disputes/triage",
    response_model=TriageResponse,
    summary="Get triage view of score disputes",
)
def get_dispute_triage(
    status: str = Query("open", description="Dispute status filter"),
    limit: int = Query(50, ge=1, description="Maximum number of items to return"),
    db: Session = Depends(get_session),
) -> TriageResponse:
    """
    Retrieve a summary of open (or otherwise filtered) score disputes,
    grouped by reason category and annotated with tier‑shift information.
    """
    result = triage_disputes(db, status=status, limit=limit)
    return TriageResponse(**result)