from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_feed_recency


router = APIRouter(prefix="/api")


class FeedRecency(BaseModel):
    feed: str
    advisory_count: int
    package_count: int
    severity_count: int
    published_last_7d: int
    published_last_30d: int
    newest_published_at: Optional[datetime]
    oldest_published_at: Optional[datetime]
    stale: bool


class FeedRecencyResponse(BaseModel):
    feeds: List[FeedRecency]
    total_feeds: int


@router.get(
    "/vuln/advisory-feed-recency",
    response_model=FeedRecencyResponse,
)
def advisory_feed_recency(session: Session = Depends(get_session)) -> FeedRecencyResponse:
    return get_feed_recency(session)