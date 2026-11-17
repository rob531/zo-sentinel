"""HTTP surface for the disputed tier-shift queue."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_tier_shift_queue

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class TierShiftCounts(BaseModel):
    open: int
    under_review: int
    resolved: int
    rejected: int


class TierShiftItem(BaseModel):
    server_id: str
    name: str | None = None
    current_tier: str | None = None
    proposed_tier: str
    delta: int
    reason_category: str | None = None
    status: str
    created_at: str | None = None
    top_axis: str | None = None
    top_axis_p_top: float | None = None


class TierShiftQueue(BaseModel):
    counts: TierShiftCounts
    queue: list[TierShiftItem]


@router.get("/tier-shift-queue", response_model=TierShiftQueue)
def get_disputed_tier_shift_queue(
    db: Session = Depends(get_session),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> TierShiftQueue:
    result = get_tier_shift_queue(db, status=status, limit=limit)
    if isinstance(result, TierShiftQueue):
        return result
    if hasattr(result, "model_dump"):
        return TierShiftQueue.model_validate(result.model_dump())
    return TierShiftQueue.model_validate(result)