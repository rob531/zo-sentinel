from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import RiskOverviewResponse, get_risk_overview

router = APIRouter(prefix="/api", tags=["risk_overview"])


@router.get("/risk/overview", response_model=RiskOverviewResponse)
def risk_overview_endpoint(session: Session = Depends(get_session)):
    return get_risk_overview(session)