from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import FreshnessResponse, get_freshness_dashboard

router = APIRouter(prefix="/api", tags=["scoring_freshness_dashboard"])


@router.get("/scoring/freshness", response_model=FreshnessResponse)
def scoring_freshness(session: Session = Depends(get_session)):
    return get_freshness_dashboard(session)


__all__ = ["router"]