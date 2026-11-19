"""HTTP surface for the escalation history API."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_escalation_history

router = APIRouter(prefix="/api", tags=["escalation-history"])


class Escalation(BaseModel):
    server_id: str
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated_to: str
    scored_at: datetime
    decision_rule_version: str
    model_version: str


class EscalationHistoryResponse(BaseModel):
    escalations: list[Escalation]
    by_axis: dict[str, int]
    by_escalated_to: dict[str, int]
    total: int
    window_days: int


@router.get(
    "/audit/escalations",
    response_model=EscalationHistoryResponse,
)
def get_escalations(
    axis: str | None = Query(default=None),
    days: int = Query(default=30),
    db: Session = Depends(get_session),
) -> EscalationHistoryResponse:
    window_days = max(1, min(days, 365))
    result = get_escalation_history(db, axis=axis, days=window_days)
    return EscalationHistoryResponse(**result)