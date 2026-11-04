"""HTTP surface for the axis label-spread service.

Exposes the APIRouter mounted by the service spine and delegates all data
access to the service logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import axis_label_spread
except ImportError:  # pragma: no cover - compatibility with the staged naming variant
    from .logic import get_axis_label_spread as axis_label_spread


router = APIRouter(prefix="/api/axis", tags=["axis"])

ALLOWED_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


class LabelSpreadItem(BaseModel):
    label: str
    label_index: int
    count: int
    mean_p_top: float
    mean_p_critical: float
    escalated_ratio: float


class AxisLabelSpread(BaseModel):
    axis_name: str
    labels: list[LabelSpreadItem]


class AxisLabelSpreadResponse(BaseModel):
    days: int
    axes: list[AxisLabelSpread]


def _normalize_axes(result: object, axis_name: str) -> list[AxisLabelSpread]:
    if isinstance(result, dict):
        axes = result.get("axes")
        if axes is None:
            axes = [result]
    else:
        axes = result

    if isinstance(axes, tuple) and len(axes) == 2:
        axes = axes[1]

    if not isinstance(axes, list):
        axes = [axes]

    normalized: list[AxisLabelSpread] = []
    for axis in axes:
        if not isinstance(axis, dict):
            axis = {
                "axis_name": axis_name,
                "labels": axis,
            }
        axis_data = dict(axis)
        axis_data.setdefault("axis_name", axis_name)
        normalized.append(AxisLabelSpread.model_validate(axis_data))

    return normalized


@router.get("/label-spread", response_model=AxisLabelSpreadResponse)
def get_axis_label_spread(
    axis_name: str,
    days: int = 30,
    db: Session = Depends(get_session),
) -> AxisLabelSpreadResponse:
    if axis_name not in ALLOWED_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name: {axis_name}",
        )

    result = axis_label_spread(db, axis_name=axis_name, days=days)
    return AxisLabelSpreadResponse(
        days=days,
        axes=_normalize_axes(result, axis_name),
    )