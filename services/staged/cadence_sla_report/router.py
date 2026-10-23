from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cadence_sla_report, CadenceSLAReportResponse

router = APIRouter(prefix="/api")


@router.get(
    "/cadence/sla-report",
    response_model=CadenceSLAReportResponse,
    summary="Get Cadence SLA Report",
)
def cadence_sla_report(session: Session = Depends(get_session)):
    """Thin wrapper that delegates to the business logic."""
    return get_cadence_sla_report(session)