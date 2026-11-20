"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the generate_spine/include_spine mount. Thin: injects the
real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are relative so the module works identically at
services.staged.<name> and services.active.<name>.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_risk_tier_distribution

router = APIRouter(prefix="/api/risk", tags=["risk"])


class RiskTierBucket(BaseModel):
    bucket: str
    count: int
    server_ids: list[str]


class RiskTierDistributionResponse(BaseModel):
    org_id: str | None
    buckets: list[RiskTierBucket]
    total_servers: int


@router.get(
    "/tier-distribution",
    response_model=RiskTierDistributionResponse,
)
def get_risk_tier_distribution_endpoint(
    org_id: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> RiskTierDistributionResponse:
    return get_risk_tier_distribution(db, org_id)