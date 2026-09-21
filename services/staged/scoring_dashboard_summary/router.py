from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_scoring_summary, ScoringSummaryResponse

router = APIRouter(prefix="/api", tags=["scoring_dashboard_summary"])


@router.get(
    "/scoring/summary",
    response_model=ScoringSummaryResponse,
    summary="Get scoring dashboard summary",
)
def scoring_summary(session: Session = Depends(get_session)):
    """
    Thin wrapper that delegates to the business logic for computing the scoring
    dashboard summary.
    """
    return get_scoring_summary(session)