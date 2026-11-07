"""HTTP surface for the registry risk-tier histogram service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import risk_tier_histogram

router = APIRouter(prefix="/api/registry", tags=["registry"])


class TierCount(BaseModel):
    tier: str
    count: int


class SourceTierCount(BaseModel):
    source: str
    tier: str
    count: int


class RiskTierHistogramResponse(BaseModel):
    source: str | None
    total: int
    unknown: int
    by_tier: list[TierCount]
    by_source_tier: list[SourceTierCount]


@router.get(
    "/risk-tier-histogram",
    response_model=RiskTierHistogramResponse,
)
def get_risk_tier_histogram(
    db: Session = Depends(get_session),
    source: str | None = Query(default=None),
) -> RiskTierHistogramResponse:
    result = risk_tier_histogram(db, source=source)
    return RiskTierHistogramResponse(
        source=source,
        total=result["total"],
        unknown=result["unknown"],
        by_tier=result["by_tier"],
        by_source_tier=result["by_source_tier"],
    )