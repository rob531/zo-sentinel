from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import trust_summary, TrustSummaryResponse

router = APIRouter(prefix="/api", tags=["trust_summary"])


@router.get(
    "/trust/summary",
    response_model=TrustSummaryResponse,
    summary="Get trust summary for the requesting organization",
)
def get_trust_summary_endpoint(session: Session = Depends(get_session)):
    """
    Endpoint that returns a trust summary for the organization associated
    with the provided API key (resolved via the session).
    """
    return trust_summary(session)