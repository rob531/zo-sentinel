from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session

from .logic import get_snapshot_sizes

router = APIRouter(prefix="/perspectives", tags=["perspectives"])


class SnapshotSize(BaseModel):
    taken_at: str
    member_count: int
    present_count: int
    missing_count: int


class SnapshotSizesResponse(BaseModel):
    days: int
    perspective_id: str | None
    snapshots: list[SnapshotSize]


@router.get("/snapshot-sizes", response_model=SnapshotSizesResponse)
def snapshot_sizes(
    perspective_id: str | None = None,
    days: int = 30,
    session=Depends(get_session),
):
    return get_snapshot_sizes(session, perspective_id, days)