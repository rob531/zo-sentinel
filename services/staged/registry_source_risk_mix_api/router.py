from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_source_risk_mix

router = APIRouter(prefix="/api", tags=["registry"])


class SourceRiskMixSource(BaseModel):
    source: str
    total: int
    tiers: dict[str, int]
    mean_trust_score: float
    last_seen_max: str | None


class SourceRiskMixTotals(BaseModel):
    servers: int
    sources: int


class SourceRiskMixResponse(BaseModel):
    sources: list[SourceRiskMixSource]
    totals: SourceRiskMixTotals


@router.get(
    "/registry/source-risk-mix",
    response_model=SourceRiskMixResponse,
)
def get_source_risk_mix(
    source: str | None = None,
    db: Session = Depends(get_session),
) -> SourceRiskMixResponse:
    return compute_source_risk_mix(source=source, session=db)