"""HTTP surface for axis drift history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_drift_history

router = APIRouter(prefix="/api", tags=["axis-drift-history"])

LEGAL_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


class AxisDriftPoint(BaseModel):
    scored_at: str
    label: str | None = None
    label_index: int | None = None
    p_top: float | None = None
    model_version: str
    decision_rule_version: str | None = None


class AxisDriftSummary(BaseModel):
    count: int
    first_label: str | None = None
    last_label: str | None = None
    delta_p_top: float | None = None
    volatility: float | None = None


class AxisDriftHistoryResponse(BaseModel):
    server_id: str
    server_name: str | None = None
    axis: str
    points: list[AxisDriftPoint]
    summary: AxisDriftSummary


@router.get("/axes/drift", response_model=AxisDriftHistoryResponse)
def get_axis_drift_history(
    server_id: str,
    axis: str,
    db: Session = Depends(get_session),
) -> AxisDriftHistoryResponse:
    if axis not in LEGAL_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported axis: {axis}",
        )

    result = axis_drift_history(db, server_id=server_id, axis=axis)
    return AxisDriftHistoryResponse.model_validate(result)