# services/staged/scoring_precision_audit/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_score_variance

router = APIRouter(prefix="/api/audit", tags=["scoring-precision-audit"])


@router.get("/scoring-precision")
def get_scoring_precision(session: Session = Depends(get_session)):
    return compute_score_variance(session)