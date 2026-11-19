"""HTTP surface for the threat intelligence indicator breakdown service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import indicator_breakdown

router = APIRouter(prefix="/api", tags=["threat-intel"])


class IndicatorTypeBreakdown(BaseModel):
    indicator_type: str
    count: int
    aggregator_count: int
    distinct_pulses: int


class SourceBreakdown(BaseModel):
    source: str
    count: int
    last_fetched_at: datetime | None = None


class IndicatorBreakdownTotals(BaseModel):
    refs: int
    aggregator_refs: int


class IndicatorBreakdownResponse(BaseModel):
    indicator_types: list[IndicatorTypeBreakdown]
    sources: list[SourceBreakdown]
    totals: IndicatorBreakdownTotals


@router.get(
    "/threat-intel/indicator-breakdown",
    response_model=IndicatorBreakdownResponse,
)
def get_indicator_breakdown(
    db: Session = Depends(get_session),
) -> IndicatorBreakdownResponse:
    return IndicatorBreakdownResponse.model_validate(indicator_breakdown(db))