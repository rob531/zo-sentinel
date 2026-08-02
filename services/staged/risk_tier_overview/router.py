from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_overview, RiskTierOverviewResponse

router = APIRouter(prefix="/api", tags=["risk_tier_overview"])


@router.get(
    "/risk/overview",
    response_model=RiskTierOverviewResponse,
    summary="Get risk tier overview",
)
def risk_tier_overview(session: Session = Depends(get_session)):
    """
    Return an overview of risk tier distribution across servers.
    """
    return get_risk_tier_overview(session)