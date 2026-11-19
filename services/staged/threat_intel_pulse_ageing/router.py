"""HTTP surface for threat-intelligence pulse ageing."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import pulse_ageing

router = APIRouter(prefix="/api", tags=["threat-intel"])


class PulseAgeingItem(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    pulse_created: datetime | None = None
    first_fetched_at: datetime
    last_fetched_at: datetime
    age_days: float
    indicator_count: int
    indicator_types: list[str]
    is_aggregator: bool
    staleness: str


class PulseAgeingTotals(BaseModel):
    pulses: int
    indicators: int
    stale_pulses: int


class PulseAgeingResponse(BaseModel):
    generated_at: datetime
    pulses: list[PulseAgeingItem]
    totals: PulseAgeingTotals


@router.get("/threat-intel/pulse-ageing", response_model=PulseAgeingResponse)
def get_pulse_ageing(db: Session = Depends(get_session)) -> PulseAgeingResponse:
    return PulseAgeingResponse.model_validate(pulse_ageing(db))