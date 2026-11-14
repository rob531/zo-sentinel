from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_feed_fetch_lag

router = APIRouter(prefix="/api/vuln", tags=["vuln"])


class FeedFetchLag(BaseModel):
    feed: str
    advisories: int
    oldest_published_at: Optional[str]
    newest_published_at: Optional[str]
    newest_fetched_at: Optional[str]
    fetch_lag_seconds: Optional[int]
    published_age_seconds: Optional[int]


class FeedFetchLagResponse(BaseModel):
    generated_at: str
    feeds: list[FeedFetchLag]
    stale_feeds: list[str]


@router.get("/fetch-lag", response_model=FeedFetchLagResponse)
def get_feed_fetch_lag(
    feed: Optional[str] = None,
    db: Session = Depends(get_session),
) -> FeedFetchLagResponse:
    generated_at = datetime.now(timezone.utc).isoformat()
    result = compute_feed_fetch_lag(db, generated_at)

    feeds = list(result.get("feeds", []))
    if feed is not None:
        feeds = [item for item in feeds if item.get("feed") == feed]

    stale_feeds = [
        item["feed"]
        for item in feeds
        if item.get("fetch_lag_seconds") is not None
        and item["fetch_lag_seconds"] > 86400
    ]

    return FeedFetchLagResponse(
        generated_at=generated_at,
        feeds=feeds,
        stale_feeds=stale_feeds,
    )