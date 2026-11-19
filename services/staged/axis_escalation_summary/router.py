"""HTTP surface for the axis escalation summary service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import summarize_escalations

router = APIRouter(prefix="/api", tags=["axis-escalation-summary"])


class EscalationTarget(BaseModel):
    escalated_to: str
    count: int


class AxisEscalation(BaseModel):
    axis_name: str
    label: str
    escalated_count: int
    server_count: int
    targets: list[EscalationTarget]


class TopEscalatedServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    escalated_axes: list[str]


class AxisEscalationSummary(BaseModel):
    window_days: int
    total_escalated: int
    by_axis: list[AxisEscalation]
    top_servers: list[TopEscalatedServer]
    generated_at: str


@router.get("/axis/escalations", response_model=AxisEscalationSummary)
def get_axis_escalations(
    window_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AxisEscalationSummary:
    return AxisEscalationSummary(
        **summarize_escalations(db, window_days=window_days)
    )