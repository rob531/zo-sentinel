"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_divergence

router = APIRouter(prefix="/api", tags=["org-axis-divergence"])

AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisDivergenceEntry(BaseModel):
    axis_name: AxisName
    mean_p_top: float
    stdev_p_top: float
    worst_axis_count: int


class AxisDivergenceResponse(BaseModel):
    org_id: str
    server_count: int
    axes: list[AxisDivergenceEntry]
    widest_spread_axis: AxisName | None
    generated_at: str


@router.get(
    "/orgs/{org_id}/axis-divergence",
    response_model=AxisDivergenceResponse,
)
def get_axis_divergence(
    org_id: str,
    min_servers: int = Query(default=1, ge=1, le=50),
    db: Session = Depends(get_session),
) -> AxisDivergenceResponse:
    result = axis_divergence(db, org_id, min_servers=min_servers)
    return AxisDivergenceResponse.model_validate(result)