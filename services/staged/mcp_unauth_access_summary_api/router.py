"""HTTP surface for the MCP unauthenticated-access summary API."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unauth_access_summary

router = APIRouter(prefix="/api", tags=["mcp-unauth-access"])


class UnauthAccessCount(BaseModel):
    unauth_access: bool
    count: int


class SourceCount(BaseModel):
    source: str
    count: int


class ExposedServer(BaseModel):
    server_id: str
    name: str
    url: str
    unauth_access: bool
    source: str
    risk_tier: str
    updated_at: datetime


class UnauthAccessSummaryResponse(BaseModel):
    total_servers: int
    by_unauth_access: list[UnauthAccessCount]
    by_source: list[SourceCount]
    exposed_servers: list[ExposedServer]


@router.get(
    "/unauth-access/summary",
    response_model=UnauthAccessSummaryResponse,
)
def get_unauth_access_summary_endpoint(
    limit: int = Query(default=100, ge=1, le=500),
    risk_tier: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> UnauthAccessSummaryResponse:
    return get_unauth_access_summary(
        db,
        limit=limit,
        risk_tier=risk_tier,
    )