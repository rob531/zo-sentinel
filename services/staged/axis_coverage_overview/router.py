"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_coverage_overview

router = APIRouter(prefix="/api", tags=["axis_coverage_overview"])


class AxisInfo(BaseModel):
    axis_name: str
    label: str
    scored_servers: int
    coverage_pct: float
    mean_p_top: float
    escalated_count: int


class AxisCoverageResponse(BaseModel):
    generated_at: datetime = Field(..., description="Timestamp when the response was generated")
    total_servers: int = Field(..., description="Total number of distinct servers in the registry")
    axes: list[AxisInfo] = Field(..., description="Coverage statistics for each axis")


@router.get("/scoring/axis-coverage", response_model=AxisCoverageResponse)
def get_axis_coverage(db: Session = Depends(get_session)) -> AxisCoverageResponse:
    """Return coverage overview for all defined axes."""
    data = axis_coverage_overview(db)
    return AxisCoverageResponse(**data)