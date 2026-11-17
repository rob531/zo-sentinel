"""HTTP surface for tier distribution by registry source."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import tier_distribution_by_source

router = APIRouter(prefix="/api/risk", tags=["risk"])


class SourceTierDistribution(BaseModel):
    registry_source: str
    total: int
    by_tier: dict[str, int]
    avg_trust_score: float | None
    high_risk_count: int
    unknown_tier_count: int


class TierDistributionTotals(BaseModel):
    servers: int
    high_risk_count: int


class TierDistributionBySourceResponse(BaseModel):
    days: int
    generated_at: datetime | str
    sources: list[SourceTierDistribution]
    totals: TierDistributionTotals


@router.get(
    "/tier-by-source",
    response_model=TierDistributionBySourceResponse,
)
def get_tier_distribution_by_source(
    days: int = Query(30),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return tier_distribution_by_source(db, days=days)