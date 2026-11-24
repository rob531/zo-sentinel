"""HTTP surface for live LLM axis drift reporting."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_axis_drift


router = APIRouter(prefix="/api/axis", tags=["axis-drift"])

AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisDriftPoint(BaseModel):
    date: str
    scored_servers: int
    mean_p_top: float
    p50_p_top: float
    crossed_up: int
    crossed_down: int


class AxisDriftAxis(BaseModel):
    axis_name: str
    series: list[AxisDriftPoint]


class AxisDriftResponse(BaseModel):
    days: int
    generated_at: str
    axes: list[AxisDriftAxis]


@router.get("/drift", response_model=AxisDriftResponse)
def get_axis_drift(
    days: int = Query(30, ge=1, le=365),
    axis: AxisName | None = Query(default=None),
    db: Session = Depends(get_session),
) -> AxisDriftResponse:
    result = compute_axis_drift(days=days, axis=axis, conn=db)

    generated_at = result.get("generated_at")
    if isinstance(generated_at, datetime):
        result["generated_at"] = generated_at.isoformat()

    return AxisDriftResponse(**result)