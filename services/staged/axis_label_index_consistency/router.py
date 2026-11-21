"""HTTP surface for the axis label-index consistency service."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import axis_label_index_consistency

router = APIRouter(prefix="/api/axes", tags=["axes"])


class LabelIndexGroup(BaseModel):
    label_index: int
    labels: list[str]
    count: int


class AxisConsistency(BaseModel):
    axis_name: str
    labels: list[LabelIndexGroup]
    modal_label: Optional[str] = None


class AxisInconsistency(BaseModel):
    axis_name: str
    label_index: int
    labels: list[str]


class AxisLabelIndexConsistencyResponse(BaseModel):
    axes: list[AxisConsistency]
    inconsistencies: list[AxisInconsistency]
    total_inconsistencies: int


@router.get(
    "/label-index-consistency",
    response_model=AxisLabelIndexConsistencyResponse,
)
def get_axis_label_index_consistency(
    db: Session = Depends(get_session),
) -> AxisLabelIndexConsistencyResponse:
    result = axis_label_index_consistency(db)
    return AxisLabelIndexConsistencyResponse(**result)