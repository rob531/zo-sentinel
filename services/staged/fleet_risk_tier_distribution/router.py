# services/staged/fleet_risk_tier_distribution/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session

from .logic import get_tier_distribution

router = APIRouter(prefix="/api", tags=["risk"])


@router.get("/risk/tier-distribution")
def tier_distribution(session: Session = Depends(get_session)):
    return get_tier_distribution(session)