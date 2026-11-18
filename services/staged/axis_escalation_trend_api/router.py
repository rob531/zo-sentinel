"""HTTP surface for the axis escalation trend API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_escalation_trend


router = APIRouter(prefix="/api/axis", tags=["axis"])


class DailyEscalationPoint(BaseModel):
    date: str
    escalated: int
    scored: int


class AxisEscalationSummary(BaseModel):
    axis_name: str
    label: str
    escalated: int
    scored: int
    escalation_rate: float
    series: list[DailyEscalationPoint]


class EscalatedToSummary(BaseModel):
    escalated_to: str
    count: int


class AxisEscalationTrendResponse(BaseModel):
    days: int
    total_escalations: int
    by_axis: list[AxisEscalationSummary]
    top_escalated_to: list[EscalatedToSummary]


@router.get(
    "/escalation-trend",
    response_model=AxisEscalationTrendResponse,
)
def get_axis_escalation_trend(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_session),
) -> AxisEscalationTrendResponse:
    return AxisEscalationTrendResponse(**axis_escalation_trend(db, days))


if __name__ == "__main__":
    print("PASS")