from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_risk_tier_breakdown

router = APIRouter(prefix="/api", tags=["registry"])


class RiskTierBreakdown(BaseModel):
    risk_tier: str
    count: int
    pct: float
    avg_trust_score: Optional[float] = None


class RegistrySourceBreakdown(BaseModel):
    registry_source: str
    count: int


class RegistryRiskTierBreakdownResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    registry_source: Optional[str] = None
    tiers: List[RiskTierBreakdown]
    sources: List[RegistrySourceBreakdown]


@router.get(
    "/registry/risk-tier-breakdown",
    response_model=RegistryRiskTierBreakdownResponse,
)
def get_risk_tier_breakdown(
    registry_source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> RegistryRiskTierBreakdownResponse:
    result = get_registry_risk_tier_breakdown(session, registry_source)
    return RegistryRiskTierBreakdownResponse.model_validate(result)