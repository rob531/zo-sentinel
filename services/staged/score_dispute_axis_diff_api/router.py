from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute
from .logic import AXIS_NAMES

router = APIRouter(prefix="/api")


class AxisDiff(BaseModel):
    axis_name: str
    stored_p_top: Optional[float]
    proposed_p_top: Optional[float]
    delta: Optional[float]
    escalated: bool


class DisputeAxisDiffResponse(BaseModel):
    dispute: Dict[str, Any]
    axes: List[AxisDiff]
    stored_overall_risk: Optional[float]
    proposed_overall_risk: Optional[str]
    delta_count: int


def _parse_proposed_axes(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            import json

            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _proposed_p_top(proposed_axes: Dict[str, Any], axis_name: str) -> Optional[float]:
    value = proposed_axes.get(axis_name)
    if isinstance(value, dict):
        value = value.get("p_top")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@router.get(
    "/disputes/{dispute_id}/axis-diff",
    response_model=DisputeAxisDiffResponse,
)
def get_dispute_axis_diff(
    dispute_id: int,
    session: Session = Depends(get_session),
) -> DisputeAxisDiffResponse:
    dispute = session.get(McpScoreDispute, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="dispute_not_found")

    proposed_axes = _parse_proposed_axes(dispute.proposed_axes)
    axis_rows: List[AxisDiff] = []
    stored_overall_risk: Optional[float] = None

    for axis_name in AXIS_NAMES:
        statement = (
            select(McpLlmAxisScore)
            .where(
                McpLlmAxisScore.server_id == dispute.server_id,
                McpLlmAxisScore.axis_name == axis_name,
            )
            .order_by(
                McpLlmAxisScore.scored_at.desc(),
                McpLlmAxisScore.id.desc(),
            )
            .limit(1)
        )
        stored = session.execute(statement).scalars().first()

        stored_p_top = (
            float(stored.p_top)
            if stored is not None and stored.p_top is not None
            else None
        )
        proposed_p_top = _proposed_p_top(proposed_axes, axis_name)
        delta = (
            proposed_p_top - stored_p_top
            if proposed_p_top is not None and stored_p_top is not None
            else None
        )

        if axis_name == "overall_risk":
            stored_overall_risk = stored_p_top

        axis_rows.append(
            AxisDiff(
                axis_name=axis_name,
                stored_p_top=stored_p_top,
                proposed_p_top=proposed_p_top,
                delta=delta,
                escalated=bool(stored.escalated) if stored is not None else False,
            )
        )

    proposed_overall_risk = (
        str(dispute.proposed_overall_risk)
        if dispute.proposed_overall_risk is not None
        else None
    )

    return DisputeAxisDiffResponse(
        dispute={
            "id": dispute.id,
            "server_id": dispute.server_id,
            "submitted_by": dispute.submitted_by,
            "proposed_overall_risk": dispute.proposed_overall_risk,
            "proposed_axes": dispute.proposed_axes,
            "reason_category": dispute.reason_category,
            "explanation": dispute.explanation,
            "status": dispute.status,
            "admin_note": dispute.admin_note,
            "created_at": dispute.created_at,
            "resolved_at": dispute.resolved_at,
        },
        axes=axis_rows,
        stored_overall_risk=stored_overall_risk,
        proposed_overall_risk=proposed_overall_risk,
        delta_count=sum(axis.delta is not None for axis in axis_rows),
    )