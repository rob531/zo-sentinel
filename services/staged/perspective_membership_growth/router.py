"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import membership_growth

router = APIRouter(
    prefix="/api/perspectives",
    tags=["perspectives"],
)


class SnapshotPoint(BaseModel):
    taken_at: datetime
    count: int


class EventStats(BaseModel):
    total: int
    unseen: int
    by_change_type: dict[str, int]


class PerspectiveGrowth(BaseModel):
    perspective_id: int
    name: str
    member_count: int
    series: list[SnapshotPoint]
    events: EventStats


class MembershipGrowthResponse(BaseModel):
    generated_at: datetime
    perspectives: list[PerspectiveGrowth]


@router.get(
    "/membership-growth",
    response_model=MembershipGrowthResponse,
)
def get_membership_growth(
    db: Session = Depends(get_session),
) -> MembershipGrowthResponse:
    return MembershipGrowthResponse(**membership_growth(db))