from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import tier_source_mix

router = APIRouter(prefix="/api/registry", tags=["registry"])


class TierBreakdown(BaseModel):
    risk_tier: str
    count: int
    avg_trust_score: float | None
    avg_confidence: float | None


class SourceBreakdown(BaseModel):
    registry_source: str
    count: int
    by_tier: dict[str, int]


class StaleOverdue(BaseModel):
    count: int
    older_than_days: int


class TierSourceMixResponse(BaseModel):
    total: int
    by_tier: list[TierBreakdown]
    by_source: list[SourceBreakdown]
    stale_overdue: StaleOverdue


@router.get("/tier-source-mix", response_model=TierSourceMixResponse)
def get_tier_source_mix(
    registry_source: str | None = Query(default=None),
    since_days: int = Query(default=30, ge=0),
    db: Session = Depends(get_session),
) -> TierSourceMixResponse:
    payload = tier_source_mix(
        db,
        registry_source=registry_source,
        since_days=since_days,
    )
    return TierSourceMixResponse(**payload)