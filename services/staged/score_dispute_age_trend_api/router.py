from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_dispute_age_trend

router = APIRouter()


class AgeBucket(BaseModel):
    bucket: str
    count: int


class ReasonCategoryCount(BaseModel):
    reason_category: str
    count: int


class DisputeAgeTrendResponse(BaseModel):
    days: int
    open_count: int
    status_counts: dict[str, int]
    age_buckets: list[AgeBucket]
    median_resolution_hours: Optional[float]
    reason_categories: list[ReasonCategoryCount]


@router.get("/disputes/aging", response_model=DisputeAgeTrendResponse)
def get_age_trend(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
) -> DisputeAgeTrendResponse:
    return get_dispute_age_trend(session, days)