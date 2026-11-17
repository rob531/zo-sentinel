"""HTTP surface for the registry tier histogram service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import build_tier_histogram

router = APIRouter(prefix="/api", tags=["registry"])


class TierHistogramResponse(BaseModel):
    total: int
    unscored: int
    tiers: list[dict]
    computed_at: str


@router.get("/registry/tier-histogram", response_model=TierHistogramResponse)
def get_tier_histogram(db: Session = Depends(get_session)) -> dict:
    return build_tier_histogram(db)