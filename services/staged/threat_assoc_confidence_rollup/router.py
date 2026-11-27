"""HTTP surface for the threat association confidence rollup service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401

from .logic import get_assoc_confidence_rollup

router = APIRouter(prefix="/api", tags=["threat"])


class ThreatTypeCount(BaseModel):
    threat_type: str
    count: int
    distinct_servers: int


class ConfidenceBand(BaseModel):
    band: str
    threat_types: list[ThreatTypeCount]
    total: int


class RiskTierCount(BaseModel):
    risk_tier: str
    associated_servers: int


class AssocConfidenceResponse(BaseModel):
    days: int
    bands: list[ConfidenceBand]
    by_risk_tier: list[RiskTierCount]
    generated_at: str


@router.get("/threat/assoc-confidence", response_model=AssocConfidenceResponse)
def get_assoc_confidence(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AssocConfidenceResponse:
    rollup = get_assoc_confidence_rollup(db, days)
    return AssocConfidenceResponse(
        days=days,
        bands=rollup["bands"],
        by_risk_tier=rollup["by_risk_tier"],
        generated_at=rollup["generated_at"],
    )