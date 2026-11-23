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
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import rescore_freshness_sla

router = APIRouter(prefix="/api", tags=["servers"])


class FreshnessBand(BaseModel):
    band: str
    count: int


class WorstOffender(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    axis_count: int
    last_scored_at: datetime
    age_days: float


class RescoreFreshnessResponse(BaseModel):
    as_of: datetime
    total_servers: int
    stale_over_30d: int
    never_scored: int
    bands: list[FreshnessBand]
    worst_offenders: list[WorstOffender]


@router.get(
    "/servers/rescore-freshness",
    response_model=RescoreFreshnessResponse,
)
def get_rescore_freshness(
    db: Session = Depends(get_session),
) -> RescoreFreshnessResponse:
    payload = rescore_freshness_sla(db)
    return RescoreFreshnessResponse.model_validate(payload)