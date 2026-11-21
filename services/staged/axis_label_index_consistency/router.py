"""HTTP surface for axis label/index consistency checks."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore  # noqa: F401
from .logic import axis_label_index_consistency

router = APIRouter(prefix="/api", tags=["axis-label-index-consistency"])


class LabelIndexGroup(BaseModel):
    label_index: int | None
    labels: list[str]
    count: int


class AxisLabels(BaseModel):
    axis_name: str
    labels: list[LabelIndexGroup]


class LabelIndexInconsistency(BaseModel):
    axis_name: str
    label_index: int | None
    labels: list[str]


class AxisLabelIndexConsistencyResponse(BaseModel):
    axes: list[AxisLabels]
    inconsistencies: list[LabelIndexInconsistency]
    total_inconsistencies: int


@router.get(
    "/axes/label-index-consistency",
    response_model=AxisLabelIndexConsistencyResponse,
)
def get_axis_label_index_consistency(
    db: Session = Depends(get_session),
) -> AxisLabelIndexConsistencyResponse:
    result = axis_label_index_consistency(db)
    return AxisLabelIndexConsistencyResponse(**result)