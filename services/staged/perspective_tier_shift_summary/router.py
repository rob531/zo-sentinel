"""HTTP surface for the perspective tier-shift summary service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import perspective_tier_shift_summary


router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveTierShift(BaseModel):
    perspective_id: str
    name: str | None
    shifts: int
    upgrades: int
    downgrades: int
    distinct_servers: int
    top_old_tier: str | None
    top_new_tier: str | None


class PerspectiveTierShiftSummaryResponse(BaseModel):
    perspectives: list[PerspectiveTierShift]


@router.get(
    "/perspectives/tier-shifts",
    response_model=PerspectiveTierShiftSummaryResponse,
)
def get_perspective_tier_shifts(
    db: Session = Depends(get_session),
) -> PerspectiveTierShiftSummaryResponse:
    perspectives = perspective_tier_shift_summary(db)
    return PerspectiveTierShiftSummaryResponse(perspectives=perspectives)