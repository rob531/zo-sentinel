"""HTTP surface for perspective membership histogram endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_membership_histogram, get_perspective_members

router = APIRouter(prefix="/api", tags=["perspectives"])

PerspectiveId = int | str | UUID
BucketLabel = Literal["0", "1-10", "11-50", "51-200", "200+"]


class PerspectiveMembership(BaseModel):
    perspective_id: PerspectiveId
    name: str
    latest_snapshot_at: datetime | None
    member_count: int
    bucket: BucketLabel


class MembershipBucket(BaseModel):
    bucket: BucketLabel
    count: int


class MembershipTotals(BaseModel):
    perspectives: int
    members: int


class MembershipHistogramResponse(BaseModel):
    perspectives: list[PerspectiveMembership]
    buckets: list[MembershipBucket]
    totals: MembershipTotals


class PerspectiveMembersResponse(BaseModel):
    perspective_id: PerspectiveId
    taken_at: datetime | None
    members: list[int | str]


@router.get(
    "/perspectives/membership-histogram",
    response_model=MembershipHistogramResponse,
)
def get_perspectives_membership_histogram(
    db: Session = Depends(get_session),
) -> MembershipHistogramResponse:
    return MembershipHistogramResponse.model_validate(
        get_membership_histogram(db)
    )


@router.get(
    "/perspectives/{perspective_id}/members",
    response_model=PerspectiveMembersResponse,
)
def get_members(
    perspective_id: str,
    db: Session = Depends(get_session),
) -> PerspectiveMembersResponse:
    return PerspectiveMembersResponse.model_validate(
        get_perspective_members(db, perspective_id)
    )