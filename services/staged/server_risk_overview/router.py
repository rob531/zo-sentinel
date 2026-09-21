from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_overview, RiskTierCount

router = APIRouter(prefix="/api")


@router.get("/risk/overview", response_model=List[RiskTierCount])
def risk_overview(session: Session = Depends(get_session)):
    return get_risk_overview(session)