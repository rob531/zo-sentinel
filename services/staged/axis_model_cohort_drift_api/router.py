"""HTTP surface for the axis model cohort drift service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import cohort_report

router = APIRouter(prefix="/api/axis", tags=["axis-model-cohorts"])


@router.get("/model-cohorts")
def get_model_cohorts(days: int = 30, db: Session = Depends(get_session)) -> dict:
    """Report live scoring-model and decision-rule cohorts."""
    return cohort_report(db, days)