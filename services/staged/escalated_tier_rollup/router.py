from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_escalated_tier_rollup as _get_escalated_tier_rollup
except ImportError:
    from .logic import escalated_tier_rollup as _get_escalated_tier_rollup

router = APIRouter(prefix="/api", tags=["scores"])


class ByAxis(BaseModel):
    axis_name: str
    escalated_to: str
    decision_rule_version: str
    count: int


class EscalationRate(BaseModel):
    axis_name: str
    escalated: int
    total: int
    rate: float


class EscalatedTierRollupResponse(BaseModel):
    total_escalated: int
    by_axis: list[ByAxis]
    escalation_rate: list[EscalationRate]


@router.get(
    "/scores/escalated-tier-rollup",
    response_model=EscalatedTierRollupResponse,
)
def get_escalated_tier_rollup(
    db: Session = Depends(get_session),
) -> EscalatedTierRollupResponse:
    result: Any = _get_escalated_tier_rollup(db)

    if isinstance(result, dict):
        payload = {
            "total_escalated": result.get("total_escalated", 0),
            "by_axis": result.get("by_axis", []),
            "escalation_rate": result.get("escalation_rate", []),
        }
    else:
        total_escalated, by_axis, escalation_rate = result
        payload = {
            "total_escalated": total_escalated,
            "by_axis": by_axis,
            "escalation_rate": escalation_rate,
        }

    return EscalatedTierRollupResponse.model_validate(payload)