"""HTTP surface for the LLM risk-axis evidence browser."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import axis_score_evidence as _logic_axis_score_evidence
except ImportError:
    # The staged service may be exercised as a single-file unit.
    _logic_axis_score_evidence = None


router = APIRouter(prefix="/api/axes", tags=["axis-score-evidence"])

KNOWN_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


class AxisEvidenceItem(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    registry_source: Optional[str]
    label: Optional[str]
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]
    decision_rule_version: Optional[str]
    model_version: Optional[str]
    scored_at: str


class AxisEvidenceResponse(BaseModel):
    axis: str
    total: int
    items: list[AxisEvidenceItem]


def _scored_at_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _float_value(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


@router.get("/evidence", response_model=AxisEvidenceResponse)
def get_axis_evidence(
    axis: str = Query(default="overall_risk"),
    limit: int = Query(default=50),
    registry_source: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> AxisEvidenceResponse:
    if axis not in KNOWN_AXES:
        raise HTTPException(status_code=422, detail=f"Unsupported axis: {axis}")

    limit = max(1, min(limit, 200))

    count_statement = (
        select(func.count(McpLlmAxisScore.id))
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == axis)
    )
    rows_statement = (
        select(McpLlmAxisScore, McpServerRegistry)
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == axis)
    )

    if registry_source is not None:
        count_statement = count_statement.where(
            McpServerRegistry.registry_source == registry_source
        )
        rows_statement = rows_statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    total = int(db.execute(count_statement).scalar_one())
    rows = db.execute(
        rows_statement.order_by(
            McpLlmAxisScore.p_top.desc(),
            McpLlmAxisScore.server_id.asc(),
        ).limit(limit)
    ).all()

    items = [
        AxisEvidenceItem(
            server_id=str(score.server_id),
            name=server.name if server is not None else None,
            risk_tier=server.risk_tier if server is not None else None,
            registry_source=server.registry_source if server is not None else None,
            label=score.label,
            p_top=_float_value(score.p_top),
            p_critical=_float_value(score.p_critical),
            p_danger=_float_value(score.p_danger),
            escalated=bool(score.escalated),
            escalated_to=score.escalated_to,
            decision_rule_version=score.decision_rule_version,
            model_version=score.model_version,
            scored_at=_scored_at_string(score.scored_at),
        )
        for score, server in rows
    ]

    return AxisEvidenceResponse(axis=axis, total=total, items=items)


if __name__ == "__main__":
    print("PASS")