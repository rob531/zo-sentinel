from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cadence_health_summary

router = APIRouter(prefix="/api")


@router.get("/cadence/health-summary")
def health_summary(session: Session = Depends(get_session)):
    return get_cadence_health_summary(session)