from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_verdict_age_buckets

router = APIRouter()


class BucketModel(BaseModel):
    bucket: str
    count: int
    by_tier: Dict[str, int]


class VerdictAgeBucketsResponse(BaseModel):
    days: int
    max_age_days: int
    buckets: List[BucketModel]
    stale_total: int
    assessed_total: int
    unassessed_total: int
    generated_at: datetime


@router.get(
    "/api/verdict/age-buckets",
    response_model=VerdictAgeBucketsResponse,
    summary="Get verdict age buckets",
)
def get_verdict_age_buckets(
    max_age_days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> VerdictAgeBucketsResponse:
    """
    Return verdict‑freshness aging statistics for MCP servers.

    - **max_age_days**: Upper bound of the age window (default 30 days).
    """
    return compute_verdict_age_buckets(max_age_days, session)