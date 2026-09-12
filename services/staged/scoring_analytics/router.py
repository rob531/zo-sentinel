# services/staged/scoring_analytics/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict
from datetime import datetime, timedelta

from app.db import get_session
from .logic import get_scoring_analytics

router = APIRouter(prefix="/api", tags=["scoring_analytics"])


class DailyCount(BaseModel):
    date: str
    count: int


class ModelVersionCount(BaseModel):
    model_version: str
    count: int


class ScoringAnalyticsResponse(BaseModel):
    total_rows: int
    distinct_servers: int
    axis_averages: Dict[str, float]
    daily_counts: List[DailyCount]
    model_versions: List[ModelVersionCount]


@router.get("/scoring/analytics", response_model=ScoringAnalyticsResponse)
def scoring_analytics_endpoint(session=Depends(get_session)):
    return get_scoring_analytics(session)