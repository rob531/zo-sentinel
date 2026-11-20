"""HTTP surface for pulse diversity reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import fetch_pulse_diversity

router = APIRouter(prefix="/api", tags=["threat-intel"])


class SourceDiversity(BaseModel):
    source: str
    indicators: int
    distinct_types: int
    distinct_pulses: int
    last_fetched_at: str | None


class IndicatorTypeCount(BaseModel):
    indicator_type: str
    count: int


class PulseDiversityResponse(BaseModel):
    total_indicators: int
    distinct_indicator_types: int
    distinct_pulses: int
    aggregator_indicator_count: int
    by_source: list[SourceDiversity]
    by_indicator_type: list[IndicatorTypeCount]


@router.get(
    "/threat-intel/pulse-diversity",
    response_model=PulseDiversityResponse,
)
def get_pulse_diversity(db: Session = Depends(get_session)) -> PulseDiversityResponse:
    return PulseDiversityResponse(**fetch_pulse_diversity(db))