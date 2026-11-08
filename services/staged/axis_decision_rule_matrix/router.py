"""router.py -- HTTP surface for the axis_decision_rule_matrix service.

Thin wrapper that injects a real SQLAlchemy Session via Depends(get_session)
and delegates all business logic to services.staged.axis_decision_rule_matrix.logic.
"""
from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_decision_rule_matrix

router = APIRouter(prefix="/api", tags=["axis_decision_rule_matrix"])


class DecisionRuleVersionResponse(BaseModel):
    decision_rule_version: str | None = Field(..., description="Decision rule version")
    model_version: str | None = Field(..., description="Model version")
    row_count: int = Field(..., description="Number of rows for this version")
    first_scored_at: datetime | None = Field(..., description="Earliest scored_at")
    last_scored_at: datetime | None = Field(..., description="Latest scored_at")
    mean_p_top: float | None = Field(..., description="Average p_top")
    escalated_to_values: list[str] = Field(
        default_factory=list, description="Distinct escalated_to values"
    )


class AxisResponse(BaseModel):
    axis_name: str = Field(..., description="Name of the axis")
    decision_rule_versions: list[DecisionRuleVersionResponse] = Field(
        default_factory=list, description="Decision‑rule versions for this axis"
    )


class AxisDecisionRuleMatrixResponse(BaseModel):
    generated_at: datetime = Field(..., description="Timestamp of generation")
    axes: list[AxisResponse] = Field(default_factory=list, description="Axes data")


@router.get(
    "/axes/decision-rule-matrix",
    response_model=AxisDecisionRuleMatrixResponse,
    summary="Retrieve the decision‑rule matrix aggregated by axis",
)
def get_axis_decision_rule_matrix(
    db: Session = Depends(get_session),
) -> AxisDecisionRuleMatrixResponse:
    """Return the aggregated decision‑rule matrix for all axes."""
    payload = axis_decision_rule_matrix(db)
    return AxisDecisionRuleMatrixResponse(**payload)