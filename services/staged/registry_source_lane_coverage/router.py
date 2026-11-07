"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import lane_coverage

router = APIRouter(prefix="/api", tags=["registry_source_lane_coverage"])


class Lane(BaseModel):
    registry_source: str
    total: int
    by_risk_tier: Dict[str, int]
    by_verdict: Dict[str, int]
    never_scored: int
    scored_share: float
    median_trust_score: Optional[float]
    oldest_last_seen: Optional[datetime]
    newest_last_seen: Optional[datetime]


class LaneCoverageResponse(BaseModel):
    generated_at: datetime
    lane_count: int
    lanes: List[Lane]


@router.get(
    "/registry/sources/lane-coverage",
    response_model=LaneCoverageResponse,
    summary="Aggregated lane coverage per registry source",
)
def get_lane_coverage(db: Session = Depends(get_session)) -> LaneCoverageResponse:
    """Return aggregated lane coverage statistics."""
    result = lane_coverage(db)
    return LaneCoverageResponse(**result)