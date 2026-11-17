"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service's logic module.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# The logic function that performs the aggregation.
# It must return an instance (or dict compatible with) AxisEscalationResponse.
from .logic import axis_escalation_rate_rollup

router = APIRouter(prefix="/api", tags=["axis_escalation_rate_rollup"])


class TierAxisEscalation(BaseModel):
    axis_name: str
    scored_count: int
    escalated_count: int
    escalation_rate: Optional[float]
    latest_scored_at: Optional[str]


class AxisEscalationResponse(BaseModel):
    generated_at: str
    axes: List[TierAxisEscalation]


@router.get("/axis/escalation-rate", response_model=AxisEscalationResponse)
def get_axis_escalation_rate(db: Session = Depends(get_session)) -> AxisEscalationResponse:
    """
    Return escalation statistics per axis.
    """
    return axis_escalation_rate_rollup(db)