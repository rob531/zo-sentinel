"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_axis_confidence_snapshot

router = APIRouter(prefix="/api/axis", tags=["axis"])


class AxisMetricsResponse(BaseModel):
    scored_servers: int
    avg_p_top: float
    avg_p_critical: float
    escalated_count: int
    escalation_rate: float
    last_scored_at: str | None = None


class AxisTotalsResponse(BaseModel):
    servers_scored: int
    scores_count: int
    axes_present: int
    axes_missing: int


class AxisConfidenceResponse(BaseModel):
    days: int
    generated_at: str
    axes: dict[str, AxisMetricsResponse]
    totals: AxisTotalsResponse
    unexpected_axes: list[str]


@router.get("/confidence", response_model=AxisConfidenceResponse)
def get_axis_confidence(
    days: int = 30,
    db: Session = Depends(get_session),
) -> AxisConfidenceResponse:
    window_days = max(1, min(days, 365))
    return AxisConfidenceResponse(**get_axis_confidence_snapshot(db, window_days))