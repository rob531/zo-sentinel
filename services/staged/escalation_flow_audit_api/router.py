"""HTTP surface for the escalation flow audit service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import escalation_flow_audit

router = APIRouter(prefix="/api", tags=["escalation-flow-audit"])


class AxisEscalation(BaseModel):
    axis_name: str
    count: int
    escalated_to: dict[str, int]


class EscalationSeriesPoint(BaseModel):
    date: str
    count: int


class EscalationFlowResponse(BaseModel):
    days: int
    total_escalated: int
    by_axis: list[AxisEscalation]
    by_tier: dict[str, int]
    series: list[EscalationSeriesPoint]


@router.get("/axis/escalation-flow", response_model=EscalationFlowResponse)
def get_escalation_flow(db: Session = Depends(get_session)) -> EscalationFlowResponse:
    result = escalation_flow_audit(db)
    return EscalationFlowResponse(**result)