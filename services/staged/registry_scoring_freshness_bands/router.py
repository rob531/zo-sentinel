from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import registry_scoring_freshness_bands

router = APIRouter(prefix="/api/registry", tags=["registry"])


class OldestServer(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    last_scored_at: str | None
    age_days: float | None


class ScoringFreshnessBandsResponse(BaseModel):
    bands: dict[str, int]
    by_source: dict[str, dict[str, int]]
    oldest: list[OldestServer]


@router.get(
    "/scoring-freshness-bands",
    response_model=ScoringFreshnessBandsResponse,
)
def get_scoring_freshness_bands(
    db: Session = Depends(get_session),
) -> ScoringFreshnessBandsResponse:
    return ScoringFreshnessBandsResponse(
        **registry_scoring_freshness_bands(db)
    )