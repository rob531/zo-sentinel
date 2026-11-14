"""HTTP surface for registry growth daily statistics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_growth_daily

router = APIRouter(prefix="/api", tags=["registry-growth"])


class GrowthDailyPoint(BaseModel):
    date: str
    new_servers: int
    cumulative: int
    sources: dict[str, int]


class GrowthDailyTotals(BaseModel):
    new_servers: int
    cumulative: int
    sources: dict[str, int]


class GrowthDailyResponse(BaseModel):
    days: int
    series: list[GrowthDailyPoint]
    totals: GrowthDailyTotals


@router.get("/registry/growth-daily", response_model=GrowthDailyResponse)
def get_registry_growth_daily(
    days: int = Query(30, ge=1),
    db: Session = Depends(get_session),
) -> GrowthDailyResponse:
    return get_growth_daily(db, days=days)