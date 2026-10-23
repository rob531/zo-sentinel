from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_detail

router = APIRouter(prefix="/api", tags=["risk"])


@router.get("/risk/tier/{server_id}")
def risk_tier_detail_endpoint(
    server_id: int,
    session: Session = Depends(get_session)
):
    return get_risk_tier_detail(session, server_id)