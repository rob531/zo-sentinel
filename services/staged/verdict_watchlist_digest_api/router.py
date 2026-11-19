"""HTTP surface for the verdict watchlist digest."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import watchlist_digest

router = APIRouter(prefix="/api", tags=["watchlist"])


class TierSummary(BaseModel):
    risk_tier: str
    count: int
    share: float


class SourceSummary(BaseModel):
    registry_source: str
    count: int
    watched: int


class StaleSummary(BaseModel):
    stale_servers: int
    threshold_hours: int


class TopWatchServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    p_top: float
    last_assessed: datetime | None


class WatchlistDigestResponse(BaseModel):
    days: int
    generated_at: datetime
    totals: dict[str, int]
    by_tier: list[TierSummary]
    by_source: list[SourceSummary]
    stale: StaleSummary
    top_watch: list[TopWatchServer]


@router.get("/watchlist/digest", response_model=WatchlistDigestResponse)
def get_watchlist_digest(
    days: int = Query(default=7),
    source: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> WatchlistDigestResponse:
    clamped_days = max(1, min(90, days))
    payload = watchlist_digest(db, days=clamped_days, source=source)
    return WatchlistDigestResponse(**payload)