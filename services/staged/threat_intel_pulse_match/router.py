"""HTTP surface for threat-intelligence pulse matching."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef  # noqa: F401

from .logic import get_threat_intel_pulses

router = APIRouter(prefix="/api", tags=["threat-intel"])


class PulseIndicator(BaseModel):
    indicator_type: str
    indicator_value: str


class ThreatIntelPulse(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    created: datetime | None = None
    indicator_count: int
    matched_servers: int
    aggregator_refs: int
    top_indicators: list[PulseIndicator]


class ThreatIntelPulsesResponse(BaseModel):
    pulses: list[ThreatIntelPulse]
    total: int


@router.get(
    "/threat-intel/pulses",
    response_model=ThreatIntelPulsesResponse,
)
def list_threat_intel_pulses(
    db: Session = Depends(get_session),
) -> ThreatIntelPulsesResponse:
    pulses = get_threat_intel_pulses(db)
    return ThreatIntelPulsesResponse(pulses=pulses, total=len(pulses))