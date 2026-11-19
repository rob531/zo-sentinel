"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_pulse_overlay

router = APIRouter(prefix="/api/threat-intel", tags=["threat-intel"])


class PulseOverlayItem(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    linked_servers: int
    critical_servers: int
    danger_servers: int
    median_p_critical: float
    pulse_created: str | None = None


class PulseOverlayResponse(BaseModel):
    days: int
    pulses: list[PulseOverlayItem]


@router.get("/pulse-overlay", response_model=PulseOverlayResponse)
def get_pulse_overlay_response(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> PulseOverlayResponse:
    result: Any = get_pulse_overlay(db, days)
    if isinstance(result, dict):
        pulses = result.get("pulses", [])
    else:
        pulses = result
    return PulseOverlayResponse(
        days=days,
        pulses=[PulseOverlayItem(**pulse) for pulse in pulses],
    )