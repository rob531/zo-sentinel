"""router.py -- HTTP surface for unauth_access_tier_correlation_api.

Thin wrapper that injects the real DB session via Depends(get_session) and
delegates the heavy lifting to the sibling ``logic.py`` module.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import tier_correlation

router = APIRouter(prefix="/api/unauth", tags=["unauth_access_tier_correlation"])


class TierInfo(BaseModel):
    tier: str
    servers: int
    unauth_rows: int
    unauth_servers: int
    unauth_ratio: float


class SourceInfo(BaseModel):
    source: str
    rows: int
    distinct_servers: int


class CorrelationResponse(BaseModel):
    tiers: List[TierInfo]
    sources: List[SourceInfo]
    unmapped_servers: int = Field(..., ge=0)
    total_unauth_rows: int = Field(..., ge=0)


@router.get(
    "/tier-correlation",
    response_model=CorrelationResponse,
    summary="Correlate unauthenticated access findings with MCP risk tiers",
)
def get_tier_correlation(
    max_rows: int = Query(
        500,
        ge=0,
        le=5000,
        description="Maximum number of rows to process (clamped to 0‑5000)",
    ),
    tier: Optional[str] = Query(
        None,
        description="Filter results to a specific risk tier (case‑insensitive)",
    ),
    db: Session = Depends(get_session),
) -> CorrelationResponse:
    """Read‑only endpoint returning analytics on unauthenticated‑access findings.

    The heavy lifting is performed by :func:`services.staged.unauth_access_tier_correlation_api.logic.tier_correlation`.
    """
    # Clamp max_rows explicitly (FastAPI already enforces bounds, but keep logic clear)
    max_rows = max(0, min(max_rows, 5000))

    result = tier_correlation(db, max_rows=max_rows, tier=tier)

    # ``result`` is expected to be a mapping compatible with ``CorrelationResponse``.
    return CorrelationResponse(**result)