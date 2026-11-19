from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_tier_transitions

router = APIRouter(prefix="/api", tags=["server-tier-transition-ledger"])


class TierTransition(BaseModel):
    server_id: str
    server_name: str | None
    from_tier: str
    to_tier: str
    at: datetime
    direction: Literal["up", "down"]


class TierTransitionsResponse(BaseModel):
    days: int
    total_transitions: int
    transitions: list[TierTransition]


@router.get("/tier/transitions", response_model=TierTransitionsResponse)
def get_transitions(
    days: int = Query(default=14, ge=1, le=365),
    limit: int = Query(default=100, ge=1),
    db: Session = Depends(get_session),
) -> TierTransitionsResponse:
    transitions = get_tier_transitions(db, days=days, limit=limit)
    return TierTransitionsResponse(
        days=days,
        total_transitions=len(transitions),
        transitions=transitions,
    )