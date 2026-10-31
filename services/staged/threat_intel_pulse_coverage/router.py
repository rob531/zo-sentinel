from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_threat_intel_pulse_coverage

router = APIRouter(prefix="/api", tags=["threat-intel"])


@router.get("/threat-intel/pulses/coverage")
def threat_intel_pulse_coverage(session: Session = Depends(get_session)):
    return get_threat_intel_pulse_coverage(session)