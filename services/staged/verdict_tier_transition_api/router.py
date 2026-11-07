"""HTTP surface for verdict tier transition reporting."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import verdict_tier_transitions

router = APIRouter(prefix="/api", tags=["verdict-tier-transition"])

KnownTier = Literal[
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "INSUFFICIENT",
]
TransitionSource = Literal[
    "new",
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "INSUFFICIENT",
]


class TierTransition(BaseModel):
    from_tier: TransitionSource = Field(alias="from")
    to_tier: KnownTier = Field(alias="to")
    count: int


class TierTotals(BaseModel):
    by_tier: dict[KnownTier, int]


class VerdictTierTransitionResponse(BaseModel):
    days: int
    transitions: list[TierTransition]
    totals: TierTotals


@router.get(
    "/verdict-transitions",
    response_model=VerdictTierTransitionResponse,
)
def get_verdict_tier_transitions(
    days: int = 30,
    db: Session = Depends(get_session),
) -> VerdictTierTransitionResponse:
    result = verdict_tier_transitions(db, days)
    return VerdictTierTransitionResponse(**result)