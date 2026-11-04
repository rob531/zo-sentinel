"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the axis network-egress scoring consumer.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import network_egress_scores

router = APIRouter(prefix="/api", tags=["axis", "network_egress"])


class NetworkEgressRow(BaseModel):
    server_id: str
    name: str | None = None
    label: str | None = None
    label_index: int | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated: bool | None = None
    escalated_to: str | None = None
    decision_rule_version: str | None = None
    model_version: str
    scored_at: datetime | None = None
    risk_tier: str | None = None


class NetworkEgressResponse(BaseModel):
    axis: str
    rows: list[NetworkEgressRow]
    count: int


@router.get(
    "/axis/network_egress",
    response_model=NetworkEgressResponse,
)
def get_network_egress_scores(
    db: Session = Depends(get_session),
    server_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> NetworkEgressResponse:
    result = network_egress_scores(db, server_id=server_id, limit=limit)
    if isinstance(result, dict):
        return NetworkEgressResponse(**result)
    return NetworkEgressResponse(
        axis="network_egress",
        rows=result,
        count=len(result),
    )