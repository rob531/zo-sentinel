from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_queue

router = APIRouter(prefix="/api", tags=["dispute_queue"])


class DisputeQueueItem(BaseModel):
    id: int
    server_id: str
    server_name: str | None
    current_risk_tier: str | None
    proposed_overall_risk: str | None
    reason_category: str | None
    explanation: str | None
    status: str
    admin_note: str | None
    created_at: datetime
    resolved_at: datetime | None
    proposed_axes: list[Any] | dict[str, Any] | None
    tier_shift: bool


class DisputeQueueResponse(BaseModel):
    count: int
    limit: int
    offset: int
    disputes: list[DisputeQueueItem]
    by_reason_category: dict[str, int]


@router.get("/disputes/queue", response_model=DisputeQueueResponse)
def get_disputes_queue(
    db: Session = Depends(get_session),
    status: str = Query("open"),
    server_id: str | None = Query(None),
    reason_category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> DisputeQueueResponse:
    result = get_dispute_queue(
        db,
        status=status,
        server_id=server_id,
        reason_category=reason_category,
        limit=limit,
        offset=offset,
    )
    return DisputeQueueResponse(**result)