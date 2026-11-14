"""HTTP surface for CVE-linked MCP server risk-tier reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cve_risk_tier_summary

router = APIRouter(prefix="/api/cve", tags=["cve"])


class TierCount(BaseModel):
    tier: str
    server_count: int
    pct: float


class SeverityTierCount(BaseModel):
    severity: str
    tier: str
    count: int


class LinkedServer(BaseModel):
    server_id: str
    name: str
    tier: str
    severity: str | None = None
    match_confidence: float


class CveRiskTierTotals(BaseModel):
    linked_servers: int
    advisories: int


class CveRiskTierResponse(BaseModel):
    tiers: list[TierCount]
    severity_cross_tab: list[SeverityTierCount]
    servers: list[LinkedServer]
    totals: CveRiskTierTotals


@router.get("/risk-tier", response_model=CveRiskTierResponse)
def get_cve_risk_tier(
    db: Session = Depends(get_session),
    severity: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> CveRiskTierResponse:
    return CveRiskTierResponse(
        **get_cve_risk_tier_summary(db, severity=severity, limit=limit)
    )