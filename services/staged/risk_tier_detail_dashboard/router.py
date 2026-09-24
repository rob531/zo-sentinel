# services/staged/risk_tier_detail_dashboard/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_tier_servers, TierServersResponse

router = APIRouter(prefix="/api", tags=["risk_tier_detail"])


@router.get("/risk/tier/{risk_tier}/servers", response_model=TierServersResponse)
def get_risk_tier_servers(
    risk_tier: str,
    session: Session = Depends(get_session)
) -> TierServersResponse:
    return get_tier_servers(session=session, risk_tier=risk_tier)