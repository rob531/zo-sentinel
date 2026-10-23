services/staged/threat_intel_indicator_coverage_api/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_indicator_coverage

router = APIRouter()


@router.get("/api/threat-intel/indicator-coverage")
def indicator_coverage(
    session: Session = Depends(get_session),
):
    return get_indicator_coverage(session)