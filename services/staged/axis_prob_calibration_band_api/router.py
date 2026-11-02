"""HTTP surface for axis probability calibration bands."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_prob_calibration_bands

router = APIRouter(prefix="/api/axis", tags=["axis"])


class CalibrationBand(BaseModel):
    lower: float
    upper: float
    count: int
    avg_p_top: float
    avg_p_critical: float
    avg_p_danger: float
    modal_label: str | None


class CalibrationResponse(BaseModel):
    axis_name: str | None
    band_size: float
    bands: list[CalibrationBand]


@router.get("/calibration", response_model=CalibrationResponse)
def get_axis_calibration(
    db: Session = Depends(get_session),
    axis_name: str | None = Query(default=None),
    band_size: float = Query(default=0.1, gt=0.0, le=1.0),
) -> CalibrationResponse:
    result = axis_prob_calibration_bands(
        db,
        axis_name=axis_name,
        band_size=band_size,
    )
    return CalibrationResponse(**result)