"""HTTP surface for the threat-intel top-indicators service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_top_indicators

router = APIRouter(prefix="/api/threat-intel", tags=["threat-intel"])


class TopIndicator(BaseModel):
    indicator_type: str
    indicator_value: str
    count: int
    pulse_count: int
    is_aggregator: bool
    last_pulse_created: datetime | None
    sources: list[str]


class IndicatorTypeCount(BaseModel):
    indicator_type: str
    count: int


class TopIndicatorsResponse(BaseModel):
    limit: int
    total_indicator_groups: int
    indicators: list[TopIndicator]
    by_type: list[IndicatorTypeCount]


@router.get("/top-indicators", response_model=TopIndicatorsResponse)
def top_indicators(
    db: Session = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=200),
    indicator_type: str | None = Query(default=None, alias="type"),
) -> TopIndicatorsResponse:
    result = get_top_indicators(
        db,
        limit=limit,
        indicator_type=indicator_type,
    )
    return TopIndicatorsResponse(**result)