from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_perspective_membership_coverage


class PerspectiveCoverageItem(BaseModel):
    id: int
    name: str
    member_count: int
    distinct_tiers: int
    tiers: Dict[str, int]
    snapshot_age_days: Optional[float]


class PerspectiveMembershipCoverageResponse(BaseModel):
    generated_at: datetime
    total_perspectives: int
    perspectives_with_snapshot: int
    uncovered_servers: int
    perspectives: List[PerspectiveCoverageItem]


router = APIRouter(prefix="/api", tags=["perspective-membership-coverage"])


@router.get(
    "/perspectives/coverage",
    response_model=PerspectiveMembershipCoverageResponse,
)
def perspective_membership_coverage(
    session: Session = Depends(get_session),
) -> PerspectiveMembershipCoverageResponse:
    return get_perspective_membership_coverage(session)


if __name__ == "__main__":
    payload = PerspectiveMembershipCoverageResponse(
        generated_at=datetime.utcnow(),
        total_perspectives=2,
        perspectives_with_snapshot=2,
        uncovered_servers=1,
        perspectives=[
            PerspectiveCoverageItem(
                id=1,
                name="Example perspective",
                member_count=2,
                distinct_tiers=2,
                tiers={"standard": 1, "elevated": 1},
                snapshot_age_days=0.0,
            ),
            PerspectiveCoverageItem(
                id=2,
                name="Second perspective",
                member_count=1,
                distinct_tiers=1,
                tiers={"standard": 1},
                snapshot_age_days=1.0,
            ),
        ],
    )
    assert payload.total_perspectives == 2
    assert payload.perspectives_with_snapshot == 2
    assert payload.uncovered_servers == 1
    assert payload.perspectives[0].member_count == 2
    print("PASS")