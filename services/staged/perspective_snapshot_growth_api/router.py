from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_snapshot_growth


router = APIRouter(prefix="/api", tags=["perspectives"])


class MemberCountPoint(BaseModel):
    taken_at: datetime
    members: int = Field(ge=0)


class PerspectiveSnapshotGrowth(BaseModel):
    perspective_id: int | str
    name: str
    snapshots: int = Field(ge=0)
    first_taken_at: datetime | None = None
    latest_taken_at: datetime | None = None
    member_counts: list[MemberCountPoint] = Field(default_factory=list)
    min_members: int | None = Field(default=None, ge=0)
    max_members: int | None = Field(default=None, ge=0)
    mean_members: float | None = Field(default=None, ge=0)
    recent_snapshots: int = Field(default=0, ge=0)


class PerspectiveSnapshotGrowthResponse(BaseModel):
    perspectives: list[PerspectiveSnapshotGrowth] = Field(default_factory=list)
    total_perspectives: int = Field(ge=0)


@router.get(
    "/perspectives/snapshot-growth",
    response_model=PerspectiveSnapshotGrowthResponse,
)
def perspective_snapshot_growth(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> Any:
    return get_snapshot_growth(session, days=days)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    assert any(
        route.path == "/api/perspectives/snapshot-growth"
        for route in test_app.routes
    )
    print("PASS")