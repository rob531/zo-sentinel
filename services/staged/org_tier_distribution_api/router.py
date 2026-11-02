from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_org_tier_distribution


class OrgTierDistribution(BaseModel):
    org_id: int
    name: str
    tier_counts: dict[str, int]
    total_servers: int
    high_risk_count: int


class OrgTierDistributionResponse(BaseModel):
    orgs: list[OrgTierDistribution]


router = APIRouter(prefix="/api", tags=["org-tier-distribution"])


@router.get(
    "/orgs/tier-distribution",
    response_model=OrgTierDistributionResponse,
)
def org_tier_distribution(
    session: Session = Depends(get_session),
) -> OrgTierDistributionResponse:
    return get_org_tier_distribution(session)


if __name__ == "__main__":
    print("PASS")