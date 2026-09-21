from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_dispute_backlog_summary, DisputeBacklogSummaryResponse

router = APIRouter(prefix="/api", tags=["dispute_backlog_summary"])


@router.get(
    "/disputes/backlog",
    response_model=DisputeBacklogSummaryResponse,
    summary="Get a summary of dispute backlog",
)
def dispute_backlog_summary(session: Session = Depends(get_session)):
    """
    Return a summary of the dispute backlog, including totals,
    breakdowns by status and reason category, and recent dispute details.
    """
    return get_dispute_backlog_summary(session)