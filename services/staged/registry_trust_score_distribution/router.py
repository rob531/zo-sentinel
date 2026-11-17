"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import trust_score_distribution

router = APIRouter(prefix="/api/registry", tags=["registry_trust_score_distribution"])


class Stats(BaseModel):
    count: int
    scored_count: int
    unscored_count: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None


class Bucket(BaseModel):
    bucket_low: int
    bucket_high: int
    count: int


class RiskTierMix(BaseModel):
    risk_tier: str
    count: int


class TrustDistributionResponse(BaseModel):
    bucket_width: int = Field(..., alias="bucket_width")
    total: int
    stats: Stats
    buckets: list[Bucket]
    risk_tier_mix: list[RiskTierMix]


@router.get(
    "/trust-distribution",
    response_model=TrustDistributionResponse,
    summary="Distribution of registry trust scores",
)
def get_trust_distribution(
    bucket: int = Query(10, ge=1, le=100, description="Bucket width in score points"),
    db: Session = Depends(get_session),
) -> TrustDistributionResponse:
    """
    Return a histogram of `trust_score` values from `mcp_server_registry`,
    together with aggregate statistics and a risk‑tier mix.
    """
    result = trust_score_distribution(db, bucket)
    return TrustDistributionResponse(**result)