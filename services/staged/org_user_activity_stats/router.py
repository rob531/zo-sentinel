"""HTTP surface for organization user activity statistics."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_user_activity_stats

router = APIRouter(prefix="/api", tags=["org_user_activity_stats"])


class RoleCount(BaseModel):
    role: str
    count: int


class OrgActivity(BaseModel):
    org_id: int
    org_name: str
    created_at: datetime
    user_count: int
    roles: list[RoleCount]


class ActivityTotals(BaseModel):
    orgs: int
    users: int
    by_role: list[RoleCount]


class OrgUserActivityStatsResponse(BaseModel):
    orgs: list[OrgActivity]
    totals: ActivityTotals


@router.get(
    "/orgs/activity",
    response_model=OrgUserActivityStatsResponse,
)
def get_activity_stats(
    db: Session = Depends(get_session),
) -> OrgUserActivityStatsResponse:
    return OrgUserActivityStatsResponse(**get_org_user_activity_stats(db))