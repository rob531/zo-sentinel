from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_trend_v2

router = APIRouter(prefix="/api", tags=["risk_tier_trend_v2"])


@router.get("/risk/trend/v2")
def risk_tier_trend_v2(
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
):
    """
    Retrieve the risk‑tier transition trend for the past ``days`` days.

    Returns a JSON object with the shape:
    {
        "days": <int>,
        "series": [
            {"date": "YYYY‑MM‑DD", "tier": <str>, "count": <int>, "delta": <int>},
            ...
        ]
    }
    """
    return get_risk_tier_trend_v2(days, session)