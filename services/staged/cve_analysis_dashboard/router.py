from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cve_dashboard

router = APIRouter(prefix="/api", tags=["cve_analysis_dashboard"])


@router.get("/cve/dashboard")
def cve_dashboard(session: Session = Depends(get_session)):
    """Return aggregated CVE dashboard data."""
    return get_cve_dashboard(session)


__all__ = ["router"]