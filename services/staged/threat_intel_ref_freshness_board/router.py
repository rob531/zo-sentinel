"""HTTP surface for threat-intel reference freshness reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import threat_intel_ref_freshness

router = APIRouter(prefix="/api", tags=["threat-intel"])


class StaleSource(BaseModel):
    source: str
    stale_count: int
    oldest_fetched_at: str


class ThreatIntelRefFreshnessResponse(BaseModel):
    total_refs: int
    stale_refs: int
    stale_pct: float
    max_age_days: int
    by_indicator_type: dict[str, int]
    by_source: dict[str, int]
    aggregator_refs: int
    aggregator_pct: float
    stale_sources: list[StaleSource]


@router.get(
    "/threat-intel/refs/freshness",
    response_model=ThreatIntelRefFreshnessResponse,
)
def get_threat_intel_ref_freshness(
    max_age_days: int = 30,
    db: Session = Depends(get_session),
) -> ThreatIntelRefFreshnessResponse:
    result = threat_intel_ref_freshness(db, max_age_days=max_age_days)
    return ThreatIntelRefFreshnessResponse(**result)