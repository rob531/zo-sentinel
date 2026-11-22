"""HTTP surface for the axis p-top heatmap API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_ptop_heatmap

router = APIRouter(prefix="/api", tags=["axes"])

_VALID_AXIS_NAMES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


class AxisLabelHeatmap(BaseModel):
    label: str
    label_index: int | None
    mean_p_top: float | None
    max_p_top: float | None
    n: int


class AxisHeatmap(BaseModel):
    axis_name: str
    labels: list[AxisLabelHeatmap]
    n: int


class AxisHeatmapResponse(BaseModel):
    axes: list[AxisHeatmap]


@router.get("/axes/heatmap", response_model=AxisHeatmapResponse)
def get_axis_heatmap(
    axis_name: str | None = None,
    db: Session = Depends(get_session),
) -> AxisHeatmapResponse:
    if axis_name is not None and axis_name not in _VALID_AXIS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name: {axis_name}",
        )

    payload = axis_ptop_heatmap(db, axis_name)
    return AxisHeatmapResponse(**payload)