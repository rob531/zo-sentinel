from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_distribution_by_source
from .schema import RiskTierDistributionBySourceResponse

router = APIRouter(prefix="/api", tags=["risk_tier_distribution_by_source"])


@router.get(
    "/risk/distribution/source",
    response_model=RiskTierDistributionBySourceResponse,
    summary="Risk tier distribution grouped by source",
)
def risk_tier_distribution_by_source(
    session: Session = Depends(get_session),
):
    """
    Retrieve the count of servers per risk tier, grouped by their registry source.
    """
    return get_distribution_by_source(session)