"""HTTP surface for the threat-intelligence indicator pulse service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import build_indicators, build_pulses

router = APIRouter(prefix="/api/threatintel", tags=["threat-intelligence"])


class PulseSummary(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    pulse_created: datetime | None = None
    indicator_count: int
    distinct_indicator_types: int
    distinct_sources: int
    aggregator_count: int
    latest_fetched_at: datetime | None = None
    indicator_types: list[str]


class PulseListResponse(BaseModel):
    days: int
    count: int
    pulses: list[PulseSummary]


class Indicator(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str
    source_url: str | None = None
    fetched_at: datetime | None = None
    is_aggregator: bool


class PulseDetailResponse(BaseModel):
    pulse_id: str
    count: int
    indicators: list[Indicator]


@router.get("/pulses", response_model=PulseListResponse)
def get_pulses(
    days: int = Query(default=30),
    db: Session = Depends(get_session),
) -> PulseListResponse:
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be greater than zero")

    pulses = build_pulses(db, days)
    return PulseListResponse(
        days=days,
        count=len(pulses),
        pulses=[PulseSummary(**pulse) for pulse in pulses],
    )


@router.get("/pulses/{pulse_id}", response_model=PulseDetailResponse)
def get_pulse(
    pulse_id: str,
    limit: int = Query(default=100, ge=1),
    db: Session = Depends(get_session),
) -> PulseDetailResponse:
    known_pulse = db.execute(
        select(ThreatIntelRef.pulse_id)
        .where(ThreatIntelRef.pulse_id == pulse_id)
        .limit(1)
    ).first()
    if known_pulse is None:
        raise HTTPException(status_code=404, detail="pulse not found")

    indicators = build_indicators(db, pulse_id, limit)
    return PulseDetailResponse(
        pulse_id=pulse_id,
        count=len(indicators),
        indicators=[Indicator(**indicator) for indicator in indicators],
    )