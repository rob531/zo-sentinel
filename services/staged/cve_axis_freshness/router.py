from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_freshness_report

router = APIRouter(prefix="/api", tags=["cve_axis_freshness"])


@router.get("/reports/cve/freshness")
def freshness_endpoint(session: Session = Depends(get_session)):
    return get_freshness_report(session)