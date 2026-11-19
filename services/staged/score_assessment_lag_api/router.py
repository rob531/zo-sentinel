"""HTTP surface for scoring assessment lag metrics."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_assessment_lag, get_server_assessment_lag

router = APIRouter(prefix="/api", tags=["scores"])


class AssessmentLagServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_scored_at: datetime | None
    lag_hours: float


class AssessmentLagResponse(BaseModel):
    buckets: dict[str, int]
    never_scored: int
    median_lag_hours: float
    servers: list[AssessmentLagServer]
    generated_at: datetime


class ServerAssessmentLagResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_scored_at: datetime | None
    lag_hours: float
    axes_scored: int


@router.get("/scores/assessment-lag", response_model=AssessmentLagResponse)
def assessment_lag(
    max_lag_hours: int = Query(default=168, ge=0),
    db: Session = Depends(get_session),
) -> AssessmentLagResponse:
    return AssessmentLagResponse(
        **get_assessment_lag(db=db, max_lag_hours=max_lag_hours)
    )


@router.get(
    "/scores/assessment-lag/servers",
    response_model=ServerAssessmentLagResponse,
)
def server_assessment_lag(
    server_id: str = Query(...),
    db: Session = Depends(get_session),
) -> ServerAssessmentLagResponse:
    return ServerAssessmentLagResponse(
        **get_server_assessment_lag(db=db, server_id=server_id)
    )