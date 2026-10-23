# services/staged/scoring_cohort_report/router.py
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["scoring_cohort_report"])


class CohortTierCounts(BaseModel):
    TRUSTED_GENERAL: int = 0
    TRUSTED_RESEARCH: int = 0
    ENTERPRISE_CONTROLLED: int = 0
    CAUTION_LIMITED: int = 0
    HIGH_RISK_ISOLATED: int = 0
    KNOWN_THREAT: int = 0
    INSUFFICIENT: int = 0


class CohortDay(BaseModel):
    date: str
    total_servers: int
    tier_counts: CohortTierCounts
    avg_axis_scores: dict[str, float]


class CohortReportResponse(BaseModel):
    days: int
    generated_at: str
    cohort: list[CohortDay]


@router.get("/scoring/cohort", response_model=CohortReportResponse)
def get_cohort_report(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> CohortReportResponse:
    from .logic import compute_cohort_report
    return compute_cohort_report(session=session, days=days)