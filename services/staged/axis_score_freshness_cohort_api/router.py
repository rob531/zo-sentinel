from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_score_freshness_cohort

router = APIRouter(prefix="/api")


@router.get("/axis/scores/freshness-cohort")
def axis_score_freshness_cohort(
    days: int = Query(default=30),
    session: Session = Depends(get_session),
):
    return get_axis_score_freshness_cohort(session=session, days=days)