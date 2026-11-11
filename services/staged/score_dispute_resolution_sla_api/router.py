from fastapi import APIRouter, Depends

from app.db import get_session
from .logic import get_dispute_resolution_sla

router = APIRouter()


@router.get("/api/disputes/resolution-sla")
def get_score_dispute_resolution_sla(
    days: int = 30,
    session=Depends(get_session),
):
    clamped_days = max(1, min(365, days))
    return get_dispute_resolution_sla(session, days=clamped_days)