from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import Perspective
from .logic import get_snapshot_membership_growth as build_snapshot_membership_growth


router = APIRouter(prefix="/api", tags=["perspective-snapshot-membership-growth"])


class PerspectiveMembershipGrowth(BaseModel):
    perspective_id: str
    perspective_name: str
    snapshots: int
    first_snapshot_at: str
    latest_snapshot_at: str
    latest_member_count: int
    delta_members: int
    members_added_per_day: float
    share_of_snapshots: float


class PerspectiveMembershipGrowthTotals(BaseModel):
    perspectives: int
    snapshots: int


class PerspectiveMembershipGrowthResponse(BaseModel):
    window_days: int
    perspectives: List[PerspectiveMembershipGrowth]
    totals: PerspectiveMembershipGrowthTotals


@router.get(
    "/perspectives/snapshot-membership-growth",
    response_model=PerspectiveMembershipGrowthResponse,
)
def get_snapshot_membership_growth(
    days: int = Query(90, ge=1),
    perspective_id: Optional[str] = Query(None),
    session=Depends(get_session),
):
    if perspective_id is not None:
        exists = (
            session.query(Perspective.id)
            .filter(Perspective.id == perspective_id)
            .scalar()
        )
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"Perspective {perspective_id} does not exist",
            )

    result = build_snapshot_membership_growth(
        session,
        days=days,
        perspective_id=perspective_id,
    )

    perspectives = []
    for item in result.get("perspectives", []):
        snapshots = item.get("snapshots", item.get("snapshots_count", 0))
        perspectives.append(
            {
                "perspective_id": str(item["perspective_id"]),
                "perspective_name": str(item.get("perspective_name") or ""),
                "snapshots": snapshots,
                "first_snapshot_at": str(item["first_snapshot_at"]),
                "latest_snapshot_at": str(item["latest_snapshot_at"]),
                "latest_member_count": item["latest_member_count"],
                "delta_members": item["delta_members"],
                "members_added_per_day": item["members_added_per_day"],
                "share_of_snapshots": item["share_of_snapshots"],
            }
        )

    totals = result.get("totals", {})
    return {
        "window_days": result.get("window_days", days),
        "perspectives": perspectives,
        "totals": {
            "perspectives": totals.get("perspectives", len(perspectives)),
            "snapshots": totals.get(
                "snapshots", sum(item["snapshots"] for item in perspectives)
            ),
        },
    }