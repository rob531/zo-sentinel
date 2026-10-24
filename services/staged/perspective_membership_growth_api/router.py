from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

if __package__:
    from .logic import get_membership_growth
else:  # Allow ``python router.py`` to run the self-test directly.
    from logic import get_membership_growth


class PerspectiveMembershipGrowth(BaseModel):
    perspective_id: Any
    name: str
    org_id: Any
    snapshots: int
    current_members: int
    first_members: int
    delta: int


class MembershipGrowthResponse(BaseModel):
    days: int
    perspectives: List[PerspectiveMembershipGrowth]


router = APIRouter(prefix="/api", tags=["perspectives"])


@router.get(
    "/perspectives/membership-growth",
    response_model=MembershipGrowthResponse,
)
def get_perspective_membership_growth(
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> MembershipGrowthResponse:
    result = get_membership_growth(days, session)
    return MembershipGrowthResponse(
        days=result["days"],
        perspectives=[
            PerspectiveMembershipGrowth(**perspective)
            for perspective in result["perspectives"]
        ],
    )


if __name__ == "__main__":
    sample = MembershipGrowthResponse(
        days=30,
        perspectives=[
            PerspectiveMembershipGrowth(
                perspective_id="perspective-1",
                name="Example",
                org_id="org-1",
                snapshots=2,
                current_members=3,
                first_members=1,
                delta=2,
            )
        ],
    )
    assert sample.days == 30
    assert sample.perspectives[0].current_members == 3
    assert sample.perspectives[0].delta == 2
    print("PASS")