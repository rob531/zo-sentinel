from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_operational_health, OperationalHealthResponse

router = APIRouter(prefix="/api", tags=["operational_health_dashboard"])


@router.get(
    "/operational/health",
    response_model=OperationalHealthResponse,
    summary="Operational health dashboard",
)
def health(session: Session = Depends(get_session)):
    """Return operational health information for jobs and daemons."""
    return get_operational_health(session)