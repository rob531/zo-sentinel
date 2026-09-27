from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_compute

router = APIRouter(prefix="/api", tags=["risk_tier_compute"])


@router.get("/risk/tier/compute")
def risk_tier_compute_endpoint(session: Session = Depends(get_session)):
    return get_risk_tier_compute(session)