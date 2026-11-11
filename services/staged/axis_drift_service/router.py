"""HTTP surface for per-axis score drift."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_axis_drift

router = APIRouter(prefix="/api/axis", tags=["axis-drift"])


class AxisServerDrift(BaseModel):
    server_id: str
    name: str
    risk_tier: str | None = None
    p_top: float
    p_critical: float
    delta: float


class AxisDrift(BaseModel):
    axis_name: str
    label: str
    mean_p_top: float
    previous_mean_p_top: float
    delta: float
    critical_count: int
    servers: list[AxisServerDrift]


class AxisDriftResponse(BaseModel):
    days: int
    generated_at: datetime
    axes: list[AxisDrift]


@router.get("/drift", response_model=AxisDriftResponse)
def get_axis_drift(
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AxisDriftResponse:
    return AxisDriftResponse(**compute_axis_drift(db, days))