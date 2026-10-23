from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_overview_metrics

router = APIRouter(
    prefix="/api/overview/metrics",
    tags=["overview_metrics"],
)


@router.get("/", response_model=dict)
def overview_metrics(session: Session = Depends(get_session)):
    """Return aggregated overview metrics for the MCP server registry."""
    return get_overview_metrics(session)