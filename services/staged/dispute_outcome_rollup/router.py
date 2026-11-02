"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

from .logic import dispute_outcome_rollup

router = APIRouter(prefix="/api", tags=["dispute-outcome-rollup"])


class TierShiftOutcome(BaseModel):
    server_id: str
    proposed_overall_risk: str
    current_rier: str
    status: str


class DisputeOutcomeRollupResponse(BaseModel):
    generated_at: str
    total: int
    by_status: dict[str, int]
    by_reason_category: list[dict[str, object]]
    aging: dict[str, int]
    tier_shift_outcomes: list[TierShiftOutcome]


@router.get(
    "/disputes/outcome-rollup",
    response_model=DisputeOutcomeRollupResponse,
)
def get_dispute_outcome_rollup(
    db: Session = Depends(get_session),
) -> DisputeOutcomeRollupResponse:
    return DisputeOutcomeRollupResponse(
        **dispute_outcome_rollup(db)
    )