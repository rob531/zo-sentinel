from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_advisory_feed_coverage as _fetch_advisory_feed_coverage


router = APIRouter(prefix="/api", tags=["advisory-feed-coverage"])


class AdvisoryFeedCoverageQuery(BaseModel):
    limit: Optional[int] = Field(default=None, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class AdvisoryFeedCoverageItem(BaseModel):
    feed: str
    advisory_count: int
    linked_server_count: int
    max_published_at: Optional[datetime]
    freshness_lag_count: int


class AdvisoryFeedCoverageResponse(BaseModel):
    feeds: list[AdvisoryFeedCoverageItem]
    total_feeds: int


def get_advisory_feed_coverage(session: Session) -> dict[str, Any]:
    return _fetch_advisory_feed_coverage(session)


@router.get(
    "/vuln/advisory-feeds",
    response_model=AdvisoryFeedCoverageResponse,
)
def list_advisory_feed_coverage(
    params: AdvisoryFeedCoverageQuery = Depends(),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = get_advisory_feed_coverage(session)
    feeds = list(result.get("feeds", []))

    offset = params.offset
    if params.limit is None:
        selected_feeds = feeds[offset:]
    else:
        selected_feeds = feeds[offset : offset + params.limit]

    return {
        "feeds": selected_feeds,
        "total_feeds": int(result.get("total_feeds", len(feeds))),
    }