"""HTTP surface for dispute tier-shift distribution."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

from .logic import dispute_tier_shift_distribution

router = APIRouter(prefix="/api", tags=["disputes"])


class TopDisputedServer(BaseModel):
    server_id: str
    count: int


class TierShiftDistributionResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_reason_category: dict[str, int]
    tier_shift: dict[str, int]
    top_disputed_servers: list[TopDisputedServer]


@router.get(
    "/disputes/tier-shift-distribution",
    response_model=TierShiftDistributionResponse,
)
def get_tier_shift_distribution(
    db: Session = Depends(get_session),
) -> TierShiftDistributionResponse:
    return TierShiftDistributionResponse(**dispute_tier_shift_distribution(db))