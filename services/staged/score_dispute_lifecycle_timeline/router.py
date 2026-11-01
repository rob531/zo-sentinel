"""HTTP surface for the score dispute lifecycle timeline."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_lifecycle_timeline

router = APIRouter(prefix="/api/disputes", tags=["score-dispute-lifecycle"])


class DisputeLifecycleItem(BaseModel):
    id: int
    server_id: int
    proposed_overall_risk: float
    reason_category: str
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None
    age_hours: Optional[float] = None
    is_open: bool


class DisputeLifecycleResponse(BaseModel):
    days: int
    total: int
    by_status: dict[str, int]
    open_count: int
    items: list[DisputeLifecycleItem]


@router.get("/lifecycle", response_model=DisputeLifecycleResponse)
def get_dispute_lifecycle(
    days: int = Query(default=30, ge=1, le=365),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> DisputeLifecycleResponse:
    result = get_lifecycle_timeline(db, days=days, status=status)
    return DisputeLifecycleResponse(**result)