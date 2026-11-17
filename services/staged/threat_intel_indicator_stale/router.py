"""HTTP surface for stale threat-intelligence indicators."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_stale_indicators

router = APIRouter(
    prefix="/api/threat-intel",
    tags=["threat-intel"],
)


class StaleSourceSummary(BaseModel):
    source: str
    stale_count: int
    oldest_fetched_at: datetime | None


class StaleTypeSummary(BaseModel):
    indicator_type: str
    stale_count: int


class StaleIndicatorItem(BaseModel):
    id: int
    indicator_type: str
    indicator_value: str
    pulse_name: str | None
    source: str
    fetched_at: datetime | None
    age_hours: float


class StaleIndicatorsResponse(BaseModel):
    max_age_hours: int
    total_stale: int
    by_source: list[StaleSourceSummary]
    by_type: list[StaleTypeSummary]
    items: list[StaleIndicatorItem]


@router.get("/stale-indicators", response_model=StaleIndicatorsResponse)
def get_stale_indicators_response(
    max_age_hours: int = 72,
    db: Session = Depends(get_session),
) -> StaleIndicatorsResponse:
    result = get_stale_indicators(db, max_age_hours=max_age_hours)
    return StaleIndicatorsResponse(**result)