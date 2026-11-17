from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_scoring_coverage_status

router = APIRouter()


@router.get("/api/scoring/coverage")
def get_scoring_coverage(
    session: Session = Depends(get_session),
) -> dict:
    return get_scoring_coverage_status(session)