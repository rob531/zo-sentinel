"""HTTP surface for perspective snapshot age rollup."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_snapshot_age_rollup

router = APIRouter(
    prefix="/api/perspectives",
    tags=["perspectives"],
)


class PerspectiveSnapshotAge(BaseModel):
    perspective_id: int
    name: str
    org_id: int | None
    snapshot_count: int
    oldest_taken_at: str | None
    newest_taken_at: str | None
    newest_age_days: float | None
    latest_membership_size: int | None


class SnapshotAgeRollupResponse(BaseModel):
    perspectives: list[PerspectiveSnapshotAge]
    stale_perspectives: int


@router.get("/snapshot-age", response_model=SnapshotAgeRollupResponse)
def get_snapshot_age(
    db: Session = Depends(get_session),
) -> SnapshotAgeRollupResponse:
    return SnapshotAgeRollupResponse(**get_snapshot_age_rollup(db))