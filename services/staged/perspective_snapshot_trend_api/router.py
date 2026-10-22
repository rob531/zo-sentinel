from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from .logic import get_snapshot_trend

router = APIRouter()


class PerspectiveSnapshot(BaseModel):
    taken_at: str
    member_count: int
    added: int
    removed: int
    members: list[str]


class PerspectiveSnapshotTrendResponse(BaseModel):
    perspective_id: int
    days: int
    snapshots: list[PerspectiveSnapshot]
    net_change: int


@router.get(
    "/perspectives/snapshot-trend",
    response_model=PerspectiveSnapshotTrendResponse,
)
def get_perspective_snapshot_trend(
    perspective_id: int = Query(..., description="Perspective identifier"),
    days: int = Query(30, description="Number of days to include"),
    db=Depends(get_session),
):
    clamped_days = max(1, min(365, days))
    return get_snapshot_trend(db, perspective_id, clamped_days)