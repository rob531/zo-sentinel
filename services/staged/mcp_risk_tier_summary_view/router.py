from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_summary, RiskTierSummaryResponse

router = APIRouter(prefix="/api", tags=["mcp_risk_tier_summary_view"])


@router.get(
    "/views/risk_tier_summary",
    response_model=RiskTierSummaryResponse,
    name="risk_tier_summary",
)
def risk_tier_summary_view(session: Session = Depends(get_session)):
    """Return a summary of servers grouped by risk tier."""
    return get_risk_tier_summary(session)