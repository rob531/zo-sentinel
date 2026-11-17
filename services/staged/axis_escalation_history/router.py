"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the axis escalation history API. The real database
session is injected with Depends(get_session), while the query and grouping
logic lives in the service's relative logic module.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_escalation_history

router = APIRouter(prefix="/api/axes", tags=["axis_escalation_history"])

LEGAL_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


class AxisEscalation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated_to: Optional[str] = None
    decision_rule_version: Optional[str] = None
    model_version: str
    scored_at: Optional[datetime] = None


class ServerEscalationHistory(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    escalations: list[AxisEscalation] = []
    escalation_count: int = 0
    last_escalated_at: Optional[datetime] = None


class AxisEscalationHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    servers: list[ServerEscalationHistory] = []
    count: int = 0
    days: int
    generated_at: datetime


@router.get("/escalations", response_model=AxisEscalationHistoryResponse)
def get_escalations(
    db: Session = Depends(get_session),
    server_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1),
    axis_name: Optional[str] = Query(None),
    axis: Optional[str] = Query(None),
) -> AxisEscalationHistoryResponse:
    """Return escalated axis scores, optionally filtered by server and axis."""
    selected_axis = axis_name if axis_name is not None else axis
    if selected_axis is not None and selected_axis not in LEGAL_AXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown axis '{selected_axis}'. Allowed axes: "
                + ", ".join(sorted(LEGAL_AXES))
            ),
        )

    if axis_name is not None and axis is not None and axis_name != axis:
        raise HTTPException(status_code=400, detail="Conflicting axis filters")

    result: Any = get_escalation_history(
        db,
        server_id=server_id,
        days=days,
        axis_name=selected_axis,
    )

    if isinstance(result, dict):
        servers = result.get("servers", [])
        count = result.get("count")
        generated_at = result.get("generated_at")
    else:
        servers = result or []
        count = None
        generated_at = None

    normalized_servers = [
        ServerEscalationHistory.model_validate(server)
        if not isinstance(server, ServerEscalationHistory)
        else server
        for server in servers
    ]

    if count is None:
        count = sum(server.escalation_count for server in normalized_servers)

    return AxisEscalationHistoryResponse(
        servers=normalized_servers,
        count=count,
        days=days,
        generated_at=generated_at or datetime.utcnow(),
    )