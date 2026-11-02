"""HTTP surface for advisory freshness lag reporting."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import advisory_freshness_lag

router = APIRouter(prefix="/api", tags=["advisories"])

DEFAULT_BUDGET_HOURS = 168


class FeedFreshnessLag(BaseModel):
    feed: str
    advisory_count: int
    newest_published_at: str | None
    newest_fetched_at: str | None
    median_lag_hours: float | None
    stale_feed: bool


class AdvisoryFreshnessLagResponse(BaseModel):
    generated_at: str
    default_budget_hours: int
    overall_advisory_count: int
    feeds: list[FeedFreshnessLag]


@router.get(
    "/advisories/freshness-lag",
    response_model=AdvisoryFreshnessLagResponse,
)
def get_advisory_freshness_lag(
    db: Session = Depends(get_session),
) -> AdvisoryFreshnessLagResponse:
    payload = advisory_freshness_lag(db)
    raw_feeds = payload.get("feeds", [])
    feeds = [
        FeedFreshnessLag(
            feed=item["feed"],
            advisory_count=int(item.get("advisory_count", 0)),
            newest_published_at=item.get("newest_published_at"),
            newest_fetched_at=item.get("newest_fetched_at"),
            median_lag_hours=item.get("median_lag_hours"),
            stale_feed=bool(item.get("stale_feed", False)),
        )
        for item in raw_feeds
    ]
    overall_count = payload.get(
        "overall_advisory_count",
        payload.get("advisory_count", sum(feed.advisory_count for feed in feeds)),
    )
    generated_at = payload.get("generated_at") or datetime.now(timezone.utc).isoformat()
    default_budget_hours = int(
        payload.get("default_budget_hours", DEFAULT_BUDGET_HOURS)
    )

    return AdvisoryFreshnessLagResponse(
        generated_at=generated_at,
        default_budget_hours=default_budget_hours,
        overall_advisory_count=int(overall_count),
        feeds=feeds,
    )