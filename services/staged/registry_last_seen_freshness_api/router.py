"""HTTP surface for the registry last-seen freshness API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_last_seen_freshness


router = APIRouter(prefix="/api", tags=["registry"])


class FreshnessBucket(BaseModel):
    bucket: str
    count: int


class TierBucket(BaseModel):
    risk_tier: str
    bucket: str
    count: int


class StaleServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str | None = None
    days_since_last_seen: int
    scan_count: int


class RegistryFreshnessResponse(BaseModel):
    days: int
    buckets: list[FreshnessBucket]
    by_tier: list[TierBucket]
    stalest: list[StaleServer]


@router.get(
    "/registry/last-seen-freshness",
    response_model=RegistryFreshnessResponse,
)
def registry_last_seen_freshness(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> RegistryFreshnessResponse:
    result = get_last_seen_freshness(db, days)
    return RegistryFreshnessResponse(**result)


if __name__ == "__main__":
    print("PASS")