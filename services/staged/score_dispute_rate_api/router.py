from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_score_dispute_rate

router = APIRouter(prefix="/api")


@router.get("/disputes/rate")
def dispute_rate(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
):
    return get_score_dispute_rate(days, session)