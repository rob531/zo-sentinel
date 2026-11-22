"""router.py -- threat-intelligence reference freshness HTTP surface."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_threat_ref_pulse_age

router = APIRouter(prefix="/api", tags=["threat-ref-pulse-age"])


class AgeBucket(BaseModel):
    bucket: str
    count: int


class IndicatorTypeAge(BaseModel):
    indicator_type: str
    count: int
    stale: int


class ThreatRefPulseAgeResponse(BaseModel):
    source: str | None
    max_age_hours: int
    generated_at: str
    total: int
    fresh: int
    stale: int
    stale_pct: float
    age_buckets: list[AgeBucket]
    by_indicator_type: list[IndicatorTypeAge]
    aggregator_refs: int


@router.get(
    "/threat/refs/age",
    response_model=ThreatRefPulseAgeResponse,
)
def get_ref_age(
    source: str | None = Query(default=None),
    max_age_hours: int = Query(default=24),
    db: Session = Depends(get_session),
) -> ThreatRefPulseAgeResponse:
    clamped_max_age_hours = max(1, min(720, max_age_hours))
    result = get_threat_ref_pulse_age(
        db,
        source=source,
        max_age_hours=clamped_max_age_hours,
    )
    return ThreatRefPulseAgeResponse(**result)