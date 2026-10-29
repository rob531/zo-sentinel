from __future__ import annotations

from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_axis_drift


router = APIRouter(prefix="/api", tags=["axis-drift-watch"])


class AxisDriftOut(BaseModel):
    axis_name: str
    label_counts: dict[str, int] = Field(default_factory=dict)
    top_label: str | None = None
    drift_rate: float = 0.0
    escalated_count: int = 0
    avg_p_top: float = 0.0
    sample_size: int = 0


class AxisDriftResponse(BaseModel):
    window_days: int
    axes: list[AxisDriftOut]


@router.get("/axis/drift", response_model=AxisDriftResponse)
async def axis_drift(
    window_days: int = Query(default=30, ge=1, le=365),
    session: Any = Depends(get_session),
) -> AxisDriftResponse:
    result = get_axis_drift(session, window_days)
    if isawaitable(result):
        result = await result

    if isinstance(result, dict):
        axes = result.get("axes", [])
    else:
        axes = result

    normalized = [
        AxisDriftOut.parse_obj(axis)
        for axis in axes
    ]
    normalized.sort(key=lambda axis: axis.axis_name)

    return AxisDriftResponse(window_days=window_days, axes=normalized)