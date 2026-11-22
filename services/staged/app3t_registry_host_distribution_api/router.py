"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service's logic module.
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic function that performs the aggregation.
# It must accept a Session, limit and min_servers and return a dict matching
# the response model defined below.
from .logic import get_registry_host_distribution


router = APIRouter(prefix="/api", tags=["registry_host_distribution"])


class HostRiskTierCounts(BaseModel):
    LOW: int = Field(..., description="Count of LOW risk tier servers")
    MEDIUM: int = Field(..., description="Count of MEDIUM risk tier servers")
    HIGH: int = Field(..., description="Count of HIGH risk tier servers")


class HostInfo(BaseModel):
    host: str = Field(..., description="Registry host / organisation")
    server_count: int = Field(..., description="Number of servers on this host")
    risk_tier_counts: HostRiskTierCounts = Field(
        ..., description="Counts of servers per risk tier"
    )
    avg_trust_score: float = Field(..., description="Average trust score for the host")


class RegistryHostDistributionResponse(BaseModel):
    total_servers: int = Field(..., description="Total number of servers across all hosts")
    host_count: int = Field(..., description="Number of distinct hosts")
    hosts: List[HostInfo] = Field(..., description="Per‑host distribution details")


@router.get(
    "/registry/hosts",
    response_model=RegistryHostDistributionResponse,
    summary="Summarise MCP server distribution across registry hosts",
)
def get_registry_hosts(
    limit: int = Query(50, ge=1, description="Maximum number of host entries to return"),
    min_servers: int = Query(1, ge=0, description="Minimum servers a host must have to be included"),
    db: Session = Depends(get_session),
) -> RegistryHostDistributionResponse:
    """
    Return a summary of how MCP servers are distributed across registry hosts/organisations.
    """
    data = get_registry_host_distribution(db, limit=limit, min_servers=min_servers)
    return RegistryHostDistributionResponse(**data)