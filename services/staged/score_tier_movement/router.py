"""router.py -- score tier movement HTTP surface.

Exposes the dashboard-facing tier movement endpoint and delegates all data
access and movement calculation to the service logic.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import score_tier_movement

router = APIRouter(prefix="/api/scores", tags=["scores"])


class TierMovementBucket(BaseModel):
    date: str
    from_tier: str
    to_tier: str
    count: int


class TierMovementServer(BaseModel):
    server_id: str
    name: str
    current_tier: str
    previous_tier: str
    changed_at: datetime | None = None


class TierMovementResponse(BaseModel):
    days: int
    buckets: list[TierMovementBucket] = Field(default_factory=list)
    servers: list[TierMovementServer] = Field(default_factory=list)


@router.get("/tier-movement", response_model=TierMovementResponse)
def get_tier_movement(
    days: int = 30,
    db: Session = Depends(get_session),
) -> TierMovementResponse:
    clamped_days = max(1, min(days, 365))
    result = score_tier_movement(db, clamped_days)
    return TierMovementResponse(
        days=clamped_days,
        buckets=result.get("buckets", []),
        servers=result.get("servers", []),
    )