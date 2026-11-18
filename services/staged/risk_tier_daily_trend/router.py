"""HTTP surface for the risk-tier daily trend service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import risk_tier_daily_trend

router = APIRouter(prefix="/api", tags=["risk-tier-daily-trend"])


class RiskTierDailyPoint(BaseModel):
    date: str
    total: int
    tiers: dict[str, int]


class RiskTierDailyTrendResponse(BaseModel):
    days: int
    series: list[RiskTierDailyPoint]
    generated_at: str


@router.get(
    "/risk/tier-daily-trend",
    response_model=RiskTierDailyTrendResponse,
)
def get_risk_tier_daily_trend(
    db: Session = Depends(get_session),
    days: int = Query(30, description="Number of calendar days to include"),
) -> RiskTierDailyTrendResponse:
    clamped_days = max(1, min(days, 365))
    result = risk_tier_daily_trend(db, clamped_days)
    return RiskTierDailyTrendResponse(**result)