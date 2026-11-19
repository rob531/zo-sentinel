"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_freshness_sla

router = APIRouter(prefix="/api/scoring/axis-freshness", tags=["scoring"])


class AxisFreshness(BaseModel):
    axis_name: str
    scored_count: int
    servers_total: int
    never_scored: int
    stale_count: int
    last_scored_at: datetime | None
    age_hours: float | None


class AxisFreshnessResponse(BaseModel):
    sla_hours: int
    axes: list[AxisFreshness]
    stale_axes: list[str]


@router.get("", response_model=AxisFreshnessResponse)
def get_axis_freshness_sla(
    sla_hours: int = 24,
    db: Session = Depends(get_session),
) -> AxisFreshnessResponse:
    result: Any = axis_freshness_sla(db, sla_hours)

    if isinstance(result, AxisFreshnessResponse):
        return result
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    return AxisFreshnessResponse.model_validate(result)