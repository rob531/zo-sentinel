"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic module must provide a function that returns a dict matching the
# AgeingResponse schema defined below.
from .logic import advisory_ageing_report

router = APIRouter(prefix="/api", tags=["advisory_ingest_ageing"])


class BandCounts(BaseModel):
    fresh_7d: int = Field(..., description="Advisories with age <= 7 days")
    stale_30d: int = Field(..., description="Advisories with 7 < age <= 30 days")
    aged_90d: int = Field(..., description="Advisories with age > 30 days")


class FeedStats(BaseModel):
    feed: str
    advisory_count: int
    linked_advisory_count: int
    unlinked_advisory_count: int
    max_age_days: int
    mean_age_days: float
    bands: BandCounts


class OldestLinkedAdvisory(BaseModel):
    advisory_id: int
    feed: str
    severity: str | None = None
    summary: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    age_days: int
    link_count: int


class AgeingResponse(BaseModel):
    generated_at: datetime
    feeds: List[FeedStats]
    oldest_linked: List[OldestLinkedAdvisory]


@router.get(
    "/vuln/advisories/ageing",
    response_model=AgeingResponse,
    summary="Ageing statistics for vulnerability advisories",
)
def get_advisory_ageing(db: Session = Depends(get_session)) -> AgeingResponse:
    """
    Return ageing information for each advisory feed and the oldest linked
    advisories. The heavy lifting is performed in `services.staged.
    advisory_ingest_ageing_api.logic.advisory_ageing_report`.
    """
    data = advisory_ageing_report(db)
    # `advisory_ageing_report` must return a mapping compatible with
    # AgeingResponse; we let Pydantic perform the final validation.
    return AgeingResponse(**data)