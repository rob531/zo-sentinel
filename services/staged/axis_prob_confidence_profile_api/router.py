from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_prob_confidence_profile

router = APIRouter(prefix="/api")


class AxisProbConfidenceProfile(BaseModel):
    axis_name: str
    rows_scored: int
    mean_p_top: float | None
    mean_p_critical: float | None
    mean_p_danger: float | None
    high_confidence_rows: int
    low_confidence_rows: int
    missing_probs_rows: int


class AxisProbConfidenceProfileResponse(BaseModel):
    profiles: list[AxisProbConfidenceProfile]
    total_rows: int


@router.get(
    "/axis/prob-confidence-profile",
    response_model=AxisProbConfidenceProfileResponse,
)
def read_axis_prob_confidence_profile(
    axis_name: str | None = Query(default=None),
    model_version: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> AxisProbConfidenceProfileResponse:
    return get_prob_confidence_profile(
        session,
        axis_name=axis_name,
        model_version=model_version,
    )