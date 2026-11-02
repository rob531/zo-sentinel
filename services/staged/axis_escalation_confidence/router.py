"""HTTP surface for escalation-confidence scores."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import escalation_confidence

router = APIRouter(prefix="/api/scores", tags=["scores"])

AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class EscalationConfidenceBucket(BaseModel):
    axis_name: AxisName
    escalated: bool
    rows: int
    avg_p_top: float | None
    avg_p_critical: float | None
    avg_p_danger: float | None
    latest_scored_at: datetime | None


class EscalationConfidenceResponse(BaseModel):
    generated_at: datetime
    axes: list[EscalationConfidenceBucket]


@router.get(
    "/escalation-confidence",
    response_model=EscalationConfidenceResponse,
)
def get_escalation_confidence(
    db: Session = Depends(get_session),
) -> EscalationConfidenceResponse:
    rows = escalation_confidence(db)
    return EscalationConfidenceResponse(
        generated_at=datetime.now(timezone.utc),
        axes=[EscalationConfidenceBucket(**row) for row in rows],
    )