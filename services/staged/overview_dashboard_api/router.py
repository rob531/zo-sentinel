from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_overview_dashboard, OverviewDashboardResponse

router = APIRouter(prefix="/api", tags=["overview_dashboard"])

@router.get(
    "/dashboard/overview",
    response_model=OverviewDashboardResponse,
    summary="Get overview dashboard data",
)
def overview_dashboard(session: Session = Depends(get_session)):
    """
    Thin wrapper that forwards the request to the business logic.
    """
    return get_overview_dashboard(session)