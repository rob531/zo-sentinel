from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import axis_drift

router = APIRouter()

ALLOWED_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class DriftPoint(BaseModel):
    scored_at: datetime
    p_top: float


class AxisSeries(BaseModel):
    axis_name: str
    points: List[DriftPoint]


class AxisDelta(BaseModel):
    first: float
    last: float
    delta: float


class AxisDriftResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    days: int
    series: List[AxisSeries]
    deltas: Dict[str, AxisDelta]


@router.get("/axis/drift/{server_id}", response_model=AxisDriftResponse)
def get_axis_drift(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> AxisDriftResponse:
    result = axis_drift(server_id, days)
    return AxisDriftResponse.model_validate(result)


if __name__ == "__main__":
    print("PASS")