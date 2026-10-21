from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_risk_tier_distribution

router = APIRouter(prefix="/api", tags=["registry_risk_tier_distribution"])

@router.get("/registry/tier-distribution")
def registry_tier_distribution(session: Session = Depends(get_session)):
    return get_registry_risk_tier_distribution(session)