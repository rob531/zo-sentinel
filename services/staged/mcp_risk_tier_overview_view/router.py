from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_overview, RiskTierOverviewResponse

router = APIRouter(prefix="/api", tags=["mcp_risk_tier_overview_view"])


@router.get(
    "/risk/overview",
    response_model=RiskTierOverviewResponse,
    summary="Get risk tier overview",
)
def risk_overview(session: Session = Depends(get_session)):
    """
    Return a summary of risk tiers, verdict distribution,
    total server count, and average risk score.
    """
    return get_risk_tier_overview(session)