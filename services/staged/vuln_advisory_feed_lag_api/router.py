from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory
from .logic import get_feed_lag

router = APIRouter(prefix="/api")


class FeedLagEntry(BaseModel):
    feed: str
    advisory_count: int
    severity_counts: Dict[str, int]
    latest_published_at: Optional[datetime] = None
    latest_fetched_at: Optional[datetime] = None
    lag_hours: Optional[float] = None
    lag_band: str


class FeedLagResponse(BaseModel):
    hours: int
    feeds: List[FeedLagEntry]


@router.get("/vuln/advisory-feed-lag", response_model=FeedLagResponse)
def advisory_feed_lag(
    hours: int = Query(24, ge=1),
    session: Session = Depends(get_session),
) -> FeedLagResponse:
    result = get_feed_lag(session, hours)
    if isinstance(result, dict):
        payload = result
    else:
        payload = {"feeds": result}
    payload = {"hours": hours, **payload}
    return FeedLagResponse(**payload)


if __name__ == "__main__":
    print("PASS")