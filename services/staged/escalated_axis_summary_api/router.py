"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_escalated_axis_summary

router = APIRouter(prefix="/api", tags=["escalated_axis_summary"])


class EscalationItem(BaseModel):
    axis_name: str
    escalated_to: str
    rows: int
    servers: int
    avg_p_critical: float
    avg_p_danger: float
    last_scored_at: datetime


class TopServerItem(BaseModel):
    server_id: int
    axis_name: str
    escalated_to: str
    avg_p_critical: float


class EscalationsResponse(BaseModel):
    days: int = Field(..., ge=1, le=365)
    escalations: List[EscalationItem]
    top_servers: List[TopServerItem]
    total_escalations: int


@router.get(
    "/axis/escalations",
    response_model=EscalationsResponse,
    summary="Escalated axis summary",
)
def get_escalations(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> EscalationsResponse:
    """
    Return a summary of escalated axes for the past *days* (default 30, max 365).
    """
    data = get_escalated_axis_summary(db, days=days)
    return EscalationsResponse(**data)