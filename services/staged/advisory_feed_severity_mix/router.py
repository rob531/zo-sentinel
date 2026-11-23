"""HTTP surface for the advisory feed severity mix service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import advisory_feed_severity_mix

router = APIRouter(prefix="/api", tags=["advisory-feed-severity-mix"])


class FeedCount(BaseModel):
    feed: str
    count: int


class SeverityCount(BaseModel):
    severity: str
    count: int


class EcosystemCount(BaseModel):
    ecosystem: str
    count: int


class AdvisoryFeedSeverityMixResponse(BaseModel):
    total: int
    null_severity: int
    recent_7d: int
    by_feed: list[FeedCount]
    by_severity: list[SeverityCount]
    by_ecosystem: list[EcosystemCount]


@router.get(
    "/vuln/advisory-feed-severity-mix",
    response_model=AdvisoryFeedSeverityMixResponse,
)
def get_advisory_feed_severity_mix(
    db: Session = Depends(get_session),
) -> AdvisoryFeedSeverityMixResponse:
    result = advisory_feed_severity_mix(db)
    return AdvisoryFeedSeverityMixResponse(**result)