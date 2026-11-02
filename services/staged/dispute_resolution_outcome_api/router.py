"""HTTP surface for dispute resolution outcome reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_resolution_outcomes

router = APIRouter(prefix="/api", tags=["dispute-resolution-outcome"])


class StatusCount(BaseModel):
    status: str
    count: int


class ReasonCount(BaseModel):
    reason_category: str
    count: int


class ProposedRiskCount(BaseModel):
    proposed_overall_risk: str
    count: int


class DisputeResolutionOutcomeResponse(BaseModel):
    total_open: int
    total_resolved: int
    by_status: list[StatusCount]
    by_reason: list[ReasonCount]
    by_proposed_risk: list[ProposedRiskCount]
    oldest_open_days: int


@router.get(
    "/disputes/outcomes",
    response_model=DisputeResolutionOutcomeResponse,
)
def get_dispute_resolution_outcomes_endpoint(
    db: Session = Depends(get_session),
) -> DisputeResolutionOutcomeResponse:
    return DisputeResolutionOutcomeResponse(
        **get_dispute_resolution_outcomes(db)
    )