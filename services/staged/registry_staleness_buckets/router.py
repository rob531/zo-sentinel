"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import registry_staleness_buckets

router = APIRouter(prefix="/api/registry", tags=["registry_staleness_buckets"])


class Bucket(BaseModel):
    band: str
    min_days: int
    max_days: int | None = None  # None for open‑ended upper bound (90+)
    count: int
    tiers: Dict[str, int]


class StalestServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    days_since_seen: int


class StalenessResponse(BaseModel):
    days: int = Field(..., description="The days window used for bucketing")
    generated_at: datetime = Field(..., description="Timestamp of response generation")
    buckets: List[Bucket]
    stalest: List[StalestServer]


@router.get(
    "/staleness/buckets",
    response_model=StalenessResponse,
    summary="Bucket servers by days since last seen and list stalest servers",
)
def get_staleness_buckets(
    days: int = Query(30, ge=1, description="Number of days to look back for bucketing"),
    db: Session = Depends(get_session),
) -> StalenessResponse:
    """
    Retrieve staleness bucket statistics for the registry.

    Delegates the heavy lifting to `registry_staleness_buckets` in the logic
    module, passing the DB session and the requested `days` window.
    """
    result = registry_staleness_buckets(db, days)
    return StalenessResponse(**result)