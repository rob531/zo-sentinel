from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_service_extraction_candidate_report

router = APIRouter(prefix="/api")


@router.get("/reports/service-extraction-candidates")
def get_report(session: Session = Depends(get_session)):
    return get_service_extraction_candidate_report(session)