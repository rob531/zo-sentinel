from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_axis_volume


AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisVolumeItem(BaseModel):
    axis_name: AxisName
    rows: int
    distinct_servers: int
    distinct_model_versions: int
    distinct_rule_versions: int
    latest_scored_at: Optional[datetime]


class AxisVolumeResponse(BaseModel):
    days: int
    total_rows: int
    axes: list[AxisVolumeItem]


router = APIRouter()


@router.get("/axis/volume", response_model=AxisVolumeResponse)
def axis_volume(
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[AxisName] = Query(default=None),
    session: Session = Depends(get_session),
) -> AxisVolumeResponse:
    return get_axis_volume(session=session, days=days, axis_name=axis_name)