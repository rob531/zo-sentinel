"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_snapshot_growth

router = APIRouter(prefix="/api", tags=["perspective_snapshot_growth"])


class SnapshotInfo(BaseModel):
    taken_at: datetime
    member_count: int


class EventInfo(BaseModel):
    change_type: str
    old_tier: str | None = None
    new_tier: str | None = None
    count: int


class SnapshotGrowthResponse(BaseModel):
    perspective_id: int
    name: str
    snapshots: list[SnapshotInfo]
    member_count_delta: int
    events: list[EventInfo]
    total_events: int


@router.get(
    "/perspectives/snapshot-growth",
    response_model=SnapshotGrowthResponse,
    summary="Growth of a perspective's snapshot membership over time",
)
def snapshot_growth_endpoint(
    perspective_id: int = Query(..., description="ID of the perspective"),
    days: int = Query(30, ge=1, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> SnapshotGrowthResponse:
    """Return snapshot growth data for a perspective."""
    result = get_snapshot_growth(db, perspective_id=perspective_id, days=days)
    return SnapshotGrowthResponse(**result)