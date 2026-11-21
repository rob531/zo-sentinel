"""HTTP surface for vulnerability advisory age rollups."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_advisory_age_rollup

router = APIRouter(prefix="/api", tags=["vuln-advisory-age"])


class AgeBucketResponse(BaseModel):
    key_0_7d: int
    key_8_30d: int
    key_31_90d: int
    key_91_365d: int
    older: int


class AdvisoryAgeRollupResponse(BaseModel):
    days: int
    total: int
    by_severity: dict[str, int]
    by_feed: dict[str, int]
    by_ecosystem: dict[str, int]
    age_buckets: dict[str, int]
    oldest_published_at: Optional[datetime]
    newest_published_at: Optional[datetime]


@router.get(
    "/vulns/advisory-age",
    response_model=AdvisoryAgeRollupResponse,
)
def get_advisory_age(
    days: int = Query(default=30, ge=0),
    db: Session = Depends(get_session),
) -> AdvisoryAgeRollupResponse:
    return AdvisoryAgeRollupResponse(**get_advisory_age_rollup(db, days=days))