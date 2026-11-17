"""HTTP surface for the escalation watchlist service."""
from __future__ import annotations

from typing import TypedDict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import build_escalations

router = APIRouter(prefix="/api", tags=["escalation-watchlist"])


class EscalationItem(TypedDict):
    server_id: str
    axis_name: str
    label: str
    p_top: float | None
    p_critical: float | None
    p_danger: float | None
    escalated: bool
    escalated_to: str | None
    scored_at: str
    decision_rule_version: str | None


class EscalationsResponse(BaseModel):
    count: int
    items: list[EscalationItem]
    by_axis: dict[str, int]
    by_escalated_to: dict[str, int]


@router.get("/axis-scores/escalations", response_model=EscalationsResponse)
def get_escalations(
    axis_name: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_session),
) -> EscalationsResponse:
    result = build_escalations(db, axis_name, limit)
    return EscalationsResponse(**result)