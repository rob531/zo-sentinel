"""HTTP surface for the axis probability mass drift API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_prob_mass_drift

router = APIRouter(prefix="/api/axis", tags=["axis"])


class AxisDriftResponse(BaseModel):
    axis_name: str
    current_mean_p_top: float
    previous_mean_p_top: float
    delta_p_top: float
    max_p_critical: float
    max_p_danger: float
    sample_size: int


class ProbMassDriftResponse(BaseModel):
    days: int
    axes: list[AxisDriftResponse]


@router.get("/prob-mass-drift", response_model=ProbMassDriftResponse)
def get_axis_prob_mass_drift(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> ProbMassDriftResponse:
    result = get_prob_mass_drift(db, days)
    return ProbMassDriftResponse(**result)