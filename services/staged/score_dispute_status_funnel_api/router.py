from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from .logic import get_score_dispute_status_funnel


class StatusCount(BaseModel):
    status: str
    count: int


class FunnelTotals(BaseModel):
    disputes: int
    resolved: int
    open: int
    tier_change_requests: int
    missing_axes: int


class ResolutionHours(BaseModel):
    median: float | None
    count: int


class OpenAgeBucket(BaseModel):
    bucket: str
    count: int


class ScoreDisputeStatusFunnelResponse(BaseModel):
    generated_at: str
    by_status: list[StatusCount]
    totals: FunnelTotals
    resolution_hours: ResolutionHours
    open_age_buckets: list[OpenAgeBucket]


router = APIRouter(prefix="/api")


@router.get(
    "/disputes/funnel",
    response_model=ScoreDisputeStatusFunnelResponse,
)
def score_dispute_status_funnel(
    status: str | None = Query(default=None),
    days: int = Query(default=90, ge=1),
    session: Session = Depends(get_session),
) -> ScoreDisputeStatusFunnelResponse:
    return get_score_dispute_status_funnel(session, status, days)