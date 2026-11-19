"""HTTP surface for attestation freshness reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_attestation_freshness

router = APIRouter(prefix="/api", tags=["attestation-freshness"])


class OverallFreshness(BaseModel):
    servers: int
    fresh: int
    stale: int
    missing: int


class RiskTierFreshness(BaseModel):
    risk_tier: str
    servers: int
    fresh: int
    stale: int
    missing: int


class StalestAttestedServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    age_days: float
    attestation_id: str


class AttestationFreshnessResponse(BaseModel):
    days: int
    overall: OverallFreshness
    by_risk_tier: list[RiskTierFreshness]
    stalest: list[StalestAttestedServer]


@router.get(
    "/attestations/freshness",
    response_model=AttestationFreshnessResponse,
)
def attestation_freshness(
    days: int = Query(default=30, ge=0, le=365),
    db: Session = Depends(get_session),
) -> AttestationFreshnessResponse:
    result = get_attestation_freshness(db, days)
    return AttestationFreshnessResponse.model_validate(result)