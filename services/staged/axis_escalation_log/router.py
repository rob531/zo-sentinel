"""HTTP surface for the axis escalation log service."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_escalations

router = APIRouter(prefix="/api", tags=["axis-escalation-log"])


class AxisEscalationCount(BaseModel):
    axis_name: str
    count: int
    top_escalated_to: str | None = None
    mean_p_critical: float | None = None


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int


class EscalationSeriesPoint(BaseModel):
    date: date
    count: int


class RecentAxisEscalation(BaseModel):
    server_id: str
    server_name: str
    axis_name: str
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    escalated_to: str | None = None
    scored_at: datetime | None = None


class AxisEscalationLogResponse(BaseModel):
    total: int
    by_axis: list[AxisEscalationCount]
    by_tier: list[RiskTierCount]
    series: list[EscalationSeriesPoint]
    recent: list[RecentAxisEscalation]


@router.get("/axis/escalations", response_model=AxisEscalationLogResponse)
def get_escalation_log(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AxisEscalationLogResponse:
    return AxisEscalationLogResponse(
        **get_axis_escalations(db, days=days)
    )