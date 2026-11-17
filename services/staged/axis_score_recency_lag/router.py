"""HTTP router for axis score recency-lag reporting."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_axis_recency_lag

router = APIRouter(prefix="/api", tags=["axis"])

AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisRecencyLag(BaseModel):
    axis_name: str
    newest_scored_at: str | None
    lag_seconds: int | float | None
    rows_in_window: int


class WorstAxis(BaseModel):
    axis_name: str
    lag_seconds: int | float | None


class AxisRecencyLagResponse(BaseModel):
    generated_at: str
    window_hours: int
    axes: list[AxisRecencyLag]
    worst_axis: WorstAxis | None


@router.get("/axis/recency-lag", response_model=AxisRecencyLagResponse)
def get_axis_recency_lag(
    db: Session = Depends(get_session),
    hours: int = Query(default=24, ge=1),
    axis: AxisName | None = Query(default=None),
) -> AxisRecencyLagResponse:
    generated_at = datetime.now(timezone.utc).isoformat()
    result = compute_axis_recency_lag(db, generated_at)

    raw_axes = result.get("axes", []) or []
    if axis is not None:
        raw_axes = [item for item in raw_axes if item.get("axis_name") == axis]

    axes = [
        AxisRecencyLag(
            axis_name=item["axis_name"],
            newest_scored_at=item.get("newest_scored_at"),
            lag_seconds=item.get("lag_seconds"),
            rows_in_window=item.get(
                "rows_in_window", item.get("rows_24h", 0)
            ),
        )
        for item in raw_axes
    ]

    worst_axis = None
    if axes:
        worst = max(axes, key=lambda item: (item.lag_seconds is not None, item.lag_seconds or -1))
        worst_axis = WorstAxis(
            axis_name=worst.axis_name,
            lag_seconds=worst.lag_seconds,
        )

    return AxisRecencyLagResponse(
        generated_at=generated_at,
        window_hours=hours,
        axes=axes,
        worst_axis=worst_axis,
    )