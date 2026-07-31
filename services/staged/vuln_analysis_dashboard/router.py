from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_vuln_analysis

router = APIRouter(prefix="/api", tags=["vuln_analysis_dashboard"])


@router.get("/vuln/analysis")
def vuln_analysis(session: Session = Depends(get_session)):
    return get_vuln_analysis(session)