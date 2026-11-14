"""HTTP surface for the dispute queue age service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import dispute_queue_summary

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class OldestOpenDispute(BaseModel):
    id: int
    server_id: int
    created_at: str | None = None
    age_hours: float | None = None


class RecentDispute(BaseModel):
    id: int
    server_id: int
    status: str
    reason_category: str
    proposed_overall_risk: float | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    age_hours: float | None = None


class DisputeQueueResponse(BaseModel):
    status_filter: str | None = None
    total: int
    by_status: dict[str, int]
    by_reason_category: dict[str, int]
    open_count: int
    avg_open_age_hours: float | None = None
    oldest_open: OldestOpenDispute | None = None
    recent: list[RecentDispute]


@router.get("/queue", response_model=DisputeQueueResponse)
def get_dispute_queue(
    status: str | None = None,
    db: Session = Depends(get_session),
) -> DisputeQueueResponse:
    return DisputeQueueResponse(**dispute_queue_summary(db, status=status))