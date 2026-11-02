"""HTTP surface for the score dispute status breakdown service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_status_breakdown


router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class DailyDisputeSeriesPoint(BaseModel):
    date: str
    submitted: int
    resolved: int


class ScoreDisputeStatusBreakdownResponse(BaseModel):
    days: int
    status_counts: dict[str, int]
    tier_mismatch_count: int
    oldest_open_age_days: float | None
    submitted_series: list[DailyDisputeSeriesPoint]
    generated_at: str


@router.get(
    "/status-breakdown",
    response_model=ScoreDisputeStatusBreakdownResponse,
)
def get_score_dispute_status_breakdown(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ScoreDisputeStatusBreakdownResponse:
    result = get_status_breakdown(db, days)
    return ScoreDisputeStatusBreakdownResponse(**result)