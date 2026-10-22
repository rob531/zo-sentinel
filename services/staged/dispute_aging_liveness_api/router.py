from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_dispute_aging


router = APIRouter(prefix="/api", tags=["disputes"])


class AgeBucket(BaseModel):
    bucket: str
    count: int


class ReasonBreakdown(BaseModel):
    reason_category: str
    count: int
    mean_age_days: float


class DisputeAgingResponse(BaseModel):
    total: int
    open: int
    resolved: int
    age_buckets: list[AgeBucket]
    mean_age_days: float
    max_age_days: float
    by_reason: list[ReasonBreakdown]
    generated_at: datetime


@router.get("/disputes/aging", response_model=DisputeAgingResponse)
def dispute_aging(
    status: str = Query(default="open"),
    days: int = Query(default=30, ge=0),
    session=Depends(get_session),
) -> DisputeAgingResponse:
    return get_dispute_aging(session, status, days)


if __name__ == "__main__":
    print("PASS")