from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_scoring_pulse, ScoringPulseResponse

router = APIRouter(prefix="/api")


@router.get(
    "/scoring/pulse",
    response_model=ScoringPulseResponse,
    summary="Get scoring pulse statistics",
)
def scoring_pulse(session: Session = Depends(get_session)):
    """Thin wrapper that forwards the request to the business logic."""
    return get_scoring_pulse(session)