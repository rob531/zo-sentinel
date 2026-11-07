"""HTTP surface for the advisory freshness rollup service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import advisory_freshness_rollup

router = APIRouter(prefix="/api", tags=["advisory-freshness"])


class FeedFreshnessResponse(BaseModel):
    feed: str
    total: int
    stale_count: int
    oldest_published_at: datetime | None
    newest_published_at: datetime | None
    avg_fetch_lag_hours: float | None
    by_severity: dict[str, int]
    linked_servers: int


class AdvisoryFreshnessResponse(BaseModel):
    days: int
    generated_at: datetime
    feeds: list[FeedFreshnessResponse]


@router.get(
    "/vuln/advisory-freshness",
    response_model=AdvisoryFreshnessResponse,
)
def get_advisory_freshness(
    feed: str | None = None,
    days: int = 30,
    db: Session = Depends(get_session),
) -> AdvisoryFreshnessResponse:
    payload = advisory_freshness_rollup(db, feed=feed, days=days)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    return AdvisoryFreshnessResponse.model_validate(payload)