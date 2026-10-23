from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cve_summary_dashboard

router = APIRouter(prefix="/api/dashboard", tags=["cve_summary_dashboard_view"])


@router.get("/cve-summary", response_model=dict)
def cve_summary(session: Session = Depends(get_session)):
    """
    Return a CVE summary dashboard payload.

    The heavy‑lifting is performed in ``services.staged.cve_summary_dashboard_view.logic``.
    """
    return get_cve_summary_dashboard(session)