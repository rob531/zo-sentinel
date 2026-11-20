"""router.py -- the service's HTTP surface (one concern, one file).

Thin wrapper that injects the real Session via Depends(get_session) and
delegates to the service's logic module.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import verdict_staleness_bands

router = APIRouter(prefix="/api", tags=["registry_verdict_staleness_bands"])


class Band(BaseModel):
    band: str
    count: int
    share: float = Field(..., ge=0.0, le=1.0)


class TopStaleServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    last_assessed: Optional[str] = None


class VerdictStalenessResponse(BaseModel):
    horizon_days: int
    total: int
    bands: List[Band]
    top_stale: List[TopStaleServer]


@router.get(
    "/registry/verdict-staleness",
    response_model=VerdictStalenessResponse,
    name="registry_verdict_staleness_bands",
)
def get_verdict_staleness(
    days: int = Query(30, ge=1, description="Horizon in days for staleness bands"),
    db: Session = Depends(get_session),
) -> VerdictStalenessResponse:
    """Return verdict‑staleness band statistics for the server registry."""
    result = verdict_staleness_bands(db, days)
    return VerdictStalenessResponse(**result)