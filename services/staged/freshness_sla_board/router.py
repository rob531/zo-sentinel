"""HTTP API for freshness SLA reporting."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import compute_freshness_sla as _compute_freshness_sla
except ImportError:
    try:
        from .logic import freshness_sla_summary as _compute_freshness_sla
    except ImportError:
        try:
            from .logic import get_freshness_sla as _compute_freshness_sla
        except ImportError:
            from .logic import freshness_sla as _compute_freshness_sla

router = APIRouter(prefix="/api", tags=["freshness"])


class FreshnessSurface(BaseModel):
    surface: str
    servers: int
    stale: int
    median_age_hours: float
    p90_age_hours: float
    sla_ok: bool


class FreshnessSLAResponse(BaseModel):
    surfaces: list[FreshnessSurface]
    generated_at: datetime


@router.get("/freshness/sla", response_model=FreshnessSLAResponse)
def freshness_sla(
    surface: int | None = Query(default=None),
    db: Session = Depends(get_session),
) -> FreshnessSLAResponse:
    result: Any = _compute_freshness_sla(db, surface)
    if isinstance(result, FreshnessSLAResponse):
        return result
    if not isinstance(result, dict):
        result = {"surfaces": result}
    payload = dict(result)
    payload.setdefault("generated_at", datetime.now(timezone.utc))
    return FreshnessSLAResponse.model_validate(payload)