from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_registry_risk_distribution

router = APIRouter(prefix="/api", tags=["registry-risk-distribution"])


class RegistrySourceRiskDistribution(BaseModel):
    registry_source: str
    total: int = Field(ge=0)
    by_tier: Dict[str, int] = Field(default_factory=dict)


class RegistryRiskDistributionResponse(BaseModel):
    generated_for_days: int = Field(ge=0)
    total: int = Field(ge=0)
    by_tier: Dict[str, int] = Field(default_factory=dict)
    by_source: List[RegistrySourceRiskDistribution] = Field(default_factory=list)
    stale_count: int = Field(ge=0)
    unassessed_count: int = Field(ge=0)


@router.get(
    "/registry/risk-distribution",
    response_model=RegistryRiskDistributionResponse,
)
def registry_risk_distribution(
    registry_source: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> RegistryRiskDistributionResponse:
    payload = get_registry_risk_distribution(
        session=session,
        registry_source=registry_source,
        days=days,
    )
    return RegistryRiskDistributionResponse.model_validate(payload)


__all__ = [
    "router",
    "RegistryRiskDistributionResponse",
    "RegistrySourceRiskDistribution",
]


if __name__ == "__main__":
    print("PASS")