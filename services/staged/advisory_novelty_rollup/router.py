"""HTTP surface for advisory novelty rollups."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import novelty_rollup

router = APIRouter(prefix="/api/advisories/novelty", tags=["advisories"])


class SeverityCount(BaseModel):
    severity: str
    count: int


class EcosystemCount(BaseModel):
    ecosystem: str
    count: int


class FeedCount(BaseModel):
    feed: str
    count: int


class NoveltyRollupResponse(BaseModel):
    days: int
    total_published: int
    total_fetched: int
    backfilled: int
    by_severity: list[SeverityCount]
    by_ecosystem: list[EcosystemCount]
    by_feed: list[FeedCount]


@router.get("", response_model=NoveltyRollupResponse)
def get_novelty_rollup(
    days: int = Query(default=30),
    db: Session = Depends(get_session),
) -> NoveltyRollupResponse:
    """Return novelty counts for advisories observed during the requested window."""
    bounded_days = max(1, min(days, 365))
    result = novelty_rollup(db, bounded_days)
    return NoveltyRollupResponse(**result)