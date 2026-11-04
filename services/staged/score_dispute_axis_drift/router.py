"""HTTP surface for score dispute axis drift reporting."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_drift_report

router = APIRouter(prefix="/api", tags=["score-dispute-axis-drift"])


class AxisDelta(BaseModel):
    axis_name: str
    proposed_p_top: float | None
    current_p_top: float | None
    abs_delta: float


class DisputeAxisDrift(BaseModel):
    dispute_id: int
    server_id: str
    status: str
    reason_category: str
    proposed_overall_risk: str
    current_risk_tier: str | None
    axis_deltas: list[AxisDelta]
    mean_abs_delta: float | None
    age_hours: float | None


class DisputeAxisDriftSummary(BaseModel):
    open: int
    approved: int
    rejected: int
    escalated: int


class AxisDriftResponse(BaseModel):
    disputes: list[DisputeAxisDrift]
    summary: DisputeAxisDriftSummary
    generated_at: datetime


@router.get("/disputes/axis-drift", response_model=AxisDriftResponse)
def get_score_dispute_axis_drift(
    db: Session = Depends(get_session),
) -> AxisDriftResponse:
    report = get_axis_drift_report(db)
    return AxisDriftResponse.model_validate(report)