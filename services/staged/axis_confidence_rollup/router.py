"""HTTP surface for the axis-confidence rollup service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_confidence_rollup

router = APIRouter(prefix="/api", tags=["axis-confidence"])


class AxisConfidence(BaseModel):
    axis_name: str
    label: str
    count: int
    mean_p_top: float
    mean_p_critical: float
    escalated_count: int
    coverage: float


class AxisConfidenceRollupResponse(BaseModel):
    generated_at: str
    axes: list[AxisConfidence]


@router.get(
    "/risk/axis-confidence",
    response_model=AxisConfidenceRollupResponse,
)
def get_axis_confidence_rollup(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> AxisConfidenceRollupResponse:
    return AxisConfidenceRollupResponse.model_validate(
        axis_confidence_rollup(db, days)
    )