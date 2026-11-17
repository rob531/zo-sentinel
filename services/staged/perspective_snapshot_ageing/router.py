"""HTTP surface for perspective snapshot ageing."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_snapshot_ageing

router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveAgeing(BaseModel):
    perspective_id: str
    latest_taken_at: datetime | None
    snapshots_last_n_days: int
    unseen_events: int
    change_types: int
    age_hours: float | None
    stale: bool


class SnapshotAgeingResponse(BaseModel):
    perspectives: list[PerspectiveAgeing]
    stale_count: int
    window_days: int


@router.get(
    "/perspectives/snapshot-ageing",
    response_model=SnapshotAgeingResponse,
)
def get_perspective_snapshot_ageing(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> SnapshotAgeingResponse:
    return get_snapshot_ageing(db, days)