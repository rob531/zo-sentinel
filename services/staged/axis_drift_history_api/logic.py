"""Axis drift history data and computation layer."""

from __future__ import annotations

from datetime import datetime
from statistics import pstdev
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


LEGAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AxisDriftPoint(BaseModel):
    scored_at: Optional[datetime]
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    model_version: str
    decision_rule_version: Optional[str]


class AxisDriftSummary(BaseModel):
    count: int
    first_label: Optional[str]
    last_label: Optional[str]
    delta_p_top: Optional[float]
    volatility: float


class AxisDriftHistoryResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    axis: str
    points: List[AxisDriftPoint]
    summary: AxisDriftSummary


def get_db_session(db: Session = Depends(get_session)) -> Session:
    """Return the application's SQLAlchemy session dependency."""
    return db


def get_axis_drift_history(
    db: Session,
    server_id: str,
    axis: Optional[str] = None,
    *,
    axis_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the ordered drift history for one server and one legal axis."""
    selected_axis = axis if axis is not None else axis_name
    if selected_axis not in LEGAL_AXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"axis must be one of: {', '.join(LEGAL_AXES)}"
            ),
        )

    server_name = db.execute(
        select(McpServerRegistry.name).where(
            McpServerRegistry.server_id == server_id
        )
    ).scalar_one_or_none()

    rows = db.execute(
        select(
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == selected_axis,
        )
        .order_by(
            McpLlmAxisScore.scored_at.asc(),
            McpLlmAxisScore.id.asc(),
        )
    ).all()

    points: List[Dict[str, Any]] = [
        {
            "scored_at": row.scored_at,
            "label": row.label,
            "label_index": row.label_index,
            "p_top": row.p_top,
            "model_version": row.model_version,
            "decision_rule_version": row.decision_rule_version,
        }
        for row in rows
    ]

    p_top_values = [
        float(point["p_top"])
        for point in points
        if point["p_top"] is not None
    ]

    if len(points) >= 2 and len(p_top_values) >= 2:
        delta_p_top = p_top_values[-1] - p_top_values[0]
    else:
        delta_p_top = None

    volatility = pstdev(p_top_values) if len(p_top_values) >= 2 else 0.0

    return {
        "server_id": server_id,
        "server_name": server_name,
        "axis": selected_axis,
        "points": points,
        "summary": {
            "count": len(points),
            "first_label": points[0]["label"] if points else None,
            "last_label": points[-1]["label"] if points else None,
            "delta_p_top": delta_p_top,
            "volatility": volatility,
        },
    }


def get_drift_history(
    db: Session,
    server_id: str,
    axis: Optional[str] = None,
    *,
    axis_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility name for callers that use the shorter function name."""
    return get_axis_drift_history(
        db,
        server_id,
        axis,
        axis_name=axis_name,
    )


def get_axis_history(
    db: Session,
    server_id: str,
    axis: Optional[str] = None,
    *,
    axis_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility name for callers that use the axis-history name."""
    return get_axis_drift_history(
        db,
        server_id,
        axis,
        axis_name=axis_name,
    )


if __name__ == "__main__":
    print("PASS")