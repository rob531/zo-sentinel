"""router.py -- HTTP surface for the threat_intel_pulse_list service.

Thin wrapper that injects a real DB session via Depends(get_session) and
delegates to the service's logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_pulse_list

router = APIRouter(prefix="/api", tags=["threat_intel_pulse_list"])


class PulseInfo(BaseModel):
    pulse_id: int
    pulse_name: str | None = None
    indicator_count: int
    indicator_types: dict[str, int]
    first_seen: str | None = None
    last_seen: str | None = None
    is_aggregator: bool
    source: str
    source_url: str


class PulsesResponse(BaseModel):
    total: int
    limit: int
    pulses: list[PulseInfo]


@router.get(
    "/threat-intel/pulses",
    response_model=PulsesResponse,
    name="list_threat_intel_pulses",
)
def list_threat_intel_pulses(
    limit: int = Query(50, ge=1, le=200),
    indicator_type: str | None = Query(None),
    db: Session = Depends(get_session),
) -> PulsesResponse:
    data = get_pulse_list(db, limit=limit, indicator_type=indicator_type)
    return PulsesResponse(total=data["total"], limit=limit, pulses=data["pulses"])