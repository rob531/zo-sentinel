"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the staged axis tier drift trend service.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_tier_drift_trend

router = APIRouter(prefix="/api/axis/drift", tags=["axis-tier-drift"])


class DriftPoint(BaseModel):
    date: str
    axis: str
    count: int
    mean_p_top: float
    mean_p_critical: float
    mean_p_danger: float
    escalation_rate: float


class DriftTrendResponse(BaseModel):
    days: int
    from_: str = Field(alias="from")
    to: str
    series: list[DriftPoint]


@router.get("/trend", response_model=DriftTrendResponse)
def get_axis_drift_trend(
    db: Session = Depends(get_session),
    days: int = Query(30),
    axis: str = Query("overall_risk"),
) -> DriftTrendResponse:
    payload = axis_tier_drift_trend(db, days=days, axis=axis)
    return DriftTrendResponse(**payload)