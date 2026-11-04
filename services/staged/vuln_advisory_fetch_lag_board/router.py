"""HTTP surface for vulnerability advisory fetch-lag reporting."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import fetch_lag_board

router = APIRouter(prefix="/api/vuln/advisories", tags=["vuln-advisories"])


class FeedFetchLag(BaseModel):
    feed: str
    total: int
    fresh: int
    delayed: int
    stale: int
    expired: int
    max_lag_seconds: float | None
    oldest_published_at: datetime | None


class FetchLagSummary(BaseModel):
    total: int
    stale: int
    expired: int


class FetchLagBoardResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    feeds: list[FeedFetchLag]
    summary: FetchLagSummary


@router.get("/fetch-lag", response_model=FetchLagBoardResponse)
def get_fetch_lag_board(
    hours: int = Query(default=24, ge=1),
    db: Session = Depends(get_session),
) -> FetchLagBoardResponse:
    payload = fetch_lag_board(db, hours)
    return FetchLagBoardResponse(**payload)