"""HTTP surface for score-dispute ageing summaries."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_dispute_ageing

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class ReasonAgeing(BaseModel):
    reason_category: str
    open_count: int
    avg_age_days: float


class DisputeAgeingResponse(BaseModel):
    window_days: int
    open_count: int
    resolved_count: int
    oldest_open_days: int | None
    median_open_days: float | None
    by_reason: list[ReasonAgeing]
    status_breakdown: dict[str, int]


@router.get("/dispute-ageing", response_model=DisputeAgeingResponse)
def get_dispute_ageing(
    days: int = 90,
    db: Session = Depends(get_session),
) -> DisputeAgeingResponse:
    return DisputeAgeingResponse(**compute_dispute_ageing(db, days=days))