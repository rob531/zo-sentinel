from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_trust_distribution


class TrustBucket(BaseModel):
    bucket: str
    lo: int = 0
    hi: int = 0
    count: int


class TrustDistributionFreshness(BaseModel):
    newest_assessed: datetime | None = None
    oldest_assessed: datetime | None = None
    stale_count: int = 0


class TrustDistributionResponse(BaseModel):
    total: int
    trust_buckets: list[TrustBucket] = Field(default_factory=list)
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    risk_tier_counts: dict[str, int] = Field(default_factory=dict)
    freshness: TrustDistributionFreshness


router = APIRouter(prefix="/api")


@router.get(
    "/registry/trust-distribution",
    response_model=TrustDistributionResponse,
)
def registry_trust_distribution(
    session: Session = Depends(get_session),
) -> TrustDistributionResponse:
    return get_trust_distribution(session)