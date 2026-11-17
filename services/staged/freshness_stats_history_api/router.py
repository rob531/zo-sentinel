from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

from .logic import fetch_freshness_history


router = APIRouter(prefix="/api", tags=["freshness_stats_history_api"])


class FreshnessStatsHistoryPoint(BaseModel):
    computed_at: str
    compute_seconds: Optional[float] = None
    payload: Optional[Dict[str, Any]] = None


class FreshnessStatsHistorySurface(BaseModel):
    surface: str
    points: List[FreshnessStatsHistoryPoint] = []
    point_count: int
    latest_compute_seconds: Optional[float] = None


class FreshnessStatsHistoryResponse(BaseModel):
    days: int
    surfaces: List[FreshnessStatsHistorySurface] = []
    surface_count: int


@router.get(
    "/freshness/stats-history",
    response_model=FreshnessStatsHistoryResponse,
    response_model_exclude_none=False,
)
def get_freshness_stats_history(
    surface: Optional[str] = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
    session=Depends(get_session),
) -> Dict[str, Any]:
    return fetch_freshness_history(session, surface, days)