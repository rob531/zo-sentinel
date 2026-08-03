from datetime import date
from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

# Import the business‑logic function that actually builds the dashboard.
# The logic module lives in the same package (services/staged/vuln_management_dashboard).
from .logic import get_dashboard

router = APIRouter(prefix="/api", tags=["vuln_management_dashboard"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------
from pydantic import BaseModel


class Summary(BaseModel):
    total: int
    critical: int
    high: int
    medium: int
    low: int


class TimelineItem(BaseModel):
    date: date
    count: int


class DashboardResponse(BaseModel):
    summary: Summary
    distribution: Dict[str, int]
    timeline: List[TimelineItem]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.get(
    "/vuln/dashboard",
    response_model=DashboardResponse,
    summary="Aggregated vulnerability advisory dashboard",
)
def get_vuln_dashboard(session: Session = Depends(get_session)) -> DashboardResponse:
    """
    Returns a dashboard aggregating vulnerability advisories by severity,
    ecosystem, and creation date.
    """
    return get_dashboard(session)