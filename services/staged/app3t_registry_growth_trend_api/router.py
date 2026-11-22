"""router.py -- HTTP surface for the app3t_registry_growth_trend_api service.

Thin wrapper that injects a real DB session via Depends(get_session) and
delegates all business logic to the sibling ``logic`` module.
"""
from __future__ import annotations

from datetime import date
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_growth_trend


router = APIRouter(prefix="/api", tags=["registry_growth_trend"])


class SeriesItem(BaseModel):
    date: date
    new_servers: int
    trusted_general: int
    caution_limited: int
    high_risk_isolated: int
    insufficient: int


class GrowthTrendResponse(BaseModel):
    days: int
    series: List[SeriesItem]


@router.get(
    "/registry/growth-trend",
    response_model=GrowthTrendResponse,
    summary="Growth trend of newly seen servers",
)
def growth_trend(
    days: int = Query(7, ge=1, description="Number of recent days to include"),
    db: Session = Depends(get_session),
) -> GrowthTrendResponse:
    """
    Return per‑day counts of servers first seen in the last *days* days,
    broken down by risk tier.
    """
    result = get_registry_growth_trend(db, days)
    return GrowthTrendResponse(**result)