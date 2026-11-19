"""HTTP surface for server lifecycle churn reporting.

Exposes the router mounted under /api and delegates registry queries to
logic.py using the application's real database session.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import lifecycle_churn

router = APIRouter(prefix="/api", tags=["registry"])


class CohortResponse(BaseModel):
    cohort: str
    count: int
    avg_trust_score: float | None
    tier_mix: dict[str, int]


class LifecycleChurnResponse(BaseModel):
    days: int
    generated_at: datetime
    cohorts: list[CohortResponse]
    total: int


@router.get(
    "/registry/lifecycle/churn",
    response_model=LifecycleChurnResponse,
)
def get_lifecycle_churn(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> LifecycleChurnResponse:
    result = lifecycle_churn(db, days)
    return LifecycleChurnResponse(**result)