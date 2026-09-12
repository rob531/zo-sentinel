from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import DashboardSummaryResponse, get_dashboard_summary

router = APIRouter(prefix="/api", tags=["dashboard_summary"])


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    summary="Get aggregated dashboard summary",
)
def dashboard_summary(session: Session = Depends(get_session)):
    """
    Returns a summary of server counts by risk tier and verdict,
    together with the total number of servers.
    """
    return get_dashboard_summary(session)