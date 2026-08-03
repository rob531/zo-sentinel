from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_trend, TrendResponse

router = APIRouter(prefix="/api")


@router.get(
    "/risk/tier/trend",
    response_model=TrendResponse,
    summary="Risk tier transition trend",
    description="Returns the count of risk tier transitions per day for the past *days* days.",
)
def risk_tier_trend(
    days: int = Query(..., ge=1, description="Number of days to look back"),
    db: Session = Depends(get_session),
):
    """
    Thin wrapper that delegates to the business logic.

    Parameters
    ----------
    days: int
        Number of days to include in the trend.
    db: Session
        SQLAlchemy session provided by the application dependency.

    Returns
    -------
    TrendResponse
        Pydantic model containing the requested trend data.
    """
    return get_risk_tier_trend(db, days)