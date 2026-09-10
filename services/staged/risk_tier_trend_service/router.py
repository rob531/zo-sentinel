from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_trend
from .schema import RiskTierTrendResponse

router = APIRouter(prefix="/api")


@router.get("/risk/trend", response_model=RiskTierTrendResponse)
def risk_tier_trend_endpoint(
    days: int = Query(..., ge=1),
    db: Session = Depends(get_session),
):
    return get_risk_tier_trend(days=days, db=db)