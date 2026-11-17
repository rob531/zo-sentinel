"""HTTP surface for the registry risk-tier distribution service."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_risk_tier_distribution

router = APIRouter(prefix="/api", tags=["registry-risk-tier-distribution"])


class TierCount(BaseModel):
    tier: str
    count: int = Field(ge=0)
    share: float = Field(ge=0.0, le=1.0)


class RegistrySourceCount(BaseModel):
    registry_source: str
    count: int = Field(ge=0)


class TopServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    trust_score: float
    last_assessed: datetime


class RiskTierDistributionResponse(BaseModel):
    generated_at: datetime
    total_servers: int = Field(ge=0)
    by_tier: list[TierCount]
    by_registry_source: list[RegistrySourceCount]
    top_servers: list[TopServer]


@router.get(
    "/risk/distribution",
    response_model=RiskTierDistributionResponse,
    summary="Report the current MCP-server risk-tier distribution",
)
def get_distribution(
    registry_source: str | None = Query(
        default=None,
        description="Optional registry-source filter.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of top servers to return.",
    ),
    db: Session = Depends(get_session),
) -> RiskTierDistributionResponse:
    result: dict[str, Any] = get_risk_tier_distribution(
        db,
        registry_source=registry_source,
        limit=limit,
    )
    return RiskTierDistributionResponse.model_validate(result)