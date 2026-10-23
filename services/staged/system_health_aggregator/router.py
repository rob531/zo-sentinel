from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import AggregatedHealthResponse, get_aggregated_health

router = APIRouter(prefix="/api")


@router.get(
    "/health/aggregated",
    response_model=AggregatedHealthResponse,
    summary="Aggregated system health",
)
def health_aggregated(session: Session = Depends(get_session)):
    """Return aggregated health information for all daemons."""
    return get_aggregated_health(session)