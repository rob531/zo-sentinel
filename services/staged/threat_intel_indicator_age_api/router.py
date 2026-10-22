from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .contract import ThreatIntelIndicatorAgeResponse
from .logic import get_indicator_age

router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get(
    "/threatintel/indicator-age",
    response_model=ThreatIntelIndicatorAgeResponse,
)
def threat_intel_indicator_age(
    days: int = Query(default=30, description="Age window in days"),
    session: Session = Depends(get_session),
):
    bounded_days = max(1, min(365, days))
    return get_indicator_age(session, bounded_days)