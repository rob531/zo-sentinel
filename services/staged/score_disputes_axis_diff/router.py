"""HTTP surface for dispute axis-difference queries."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_diff

router = APIRouter(prefix="/api", tags=["score-disputes-axis-diff"])


class AxisDifference(BaseModel):
    axis_name: str
    current_label: str | None = None
    current_p_top: float | None = None
    escalated: bool | None = None
    proposed_label: str | None = None
    proposed_value: float | None = None
    delta: float | None = None
    agrees: bool


class AxisDiffResponse(BaseModel):
    dispute_id: int
    server_id: str
    server_name: str | None = None
    status: str
    reason_category: str | None = None
    current_tier: str | None = None
    current_verdict: str | None = None
    proposed_tier: str
    proposed_axes: list[dict[str, Any]]
    axes: list[AxisDifference]
    missing_proposals: list[str]
    current_axes_count: int


@router.get("/disputes/{dispute_id}/axis-diff", response_model=AxisDiffResponse)
def get_dispute_axis_diff(
    dispute_id: int,
    db: Session = Depends(get_session),
) -> AxisDiffResponse:
    payload = get_axis_diff(db, dispute_id)
    return AxisDiffResponse(**payload)