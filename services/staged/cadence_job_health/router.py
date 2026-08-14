from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cadence_job_health

router = APIRouter(prefix="/api")


@router.get("/cadence/health")
def health(session: Session = Depends(get_session)):
    """
    Returns aggregated health information for Cadence jobs.
    """
    return get_cadence_job_health(session)