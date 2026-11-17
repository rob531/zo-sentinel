from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_label_order_drift


router = APIRouter(prefix="/api")


class AxisLabelOrderDriftAxis(BaseModel):
    axis_name: str
    label_index: Optional[int]
    labels: List[str]
    rows: int
    drift_reason: str


class AxisLabelOrderDriftResponse(BaseModel):
    drift_detected: bool
    axes: List[AxisLabelOrderDriftAxis]
    total_drift_groups: int


@router.get(
    "/axis/label-order-drift",
    response_model=AxisLabelOrderDriftResponse,
)
def axis_label_order_drift(
    axis_name: Optional[str] = None,
    session: Session = Depends(get_session),
) -> AxisLabelOrderDriftResponse:
    return get_axis_label_order_drift(
        session=session,
        axis_name=axis_name,
    )