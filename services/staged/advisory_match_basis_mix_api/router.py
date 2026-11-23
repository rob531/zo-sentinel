"""HTTP surface for vulnerability advisory match-basis mix reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_match_basis_mix

router = APIRouter(prefix="/api", tags=["vuln-match-basis"])


class MatchBasisStat(BaseModel):
    match_basis: str
    count: int
    avg_confidence: float
    min_confidence: float
    max_confidence: float


class ConfidenceBandStat(BaseModel):
    band: str
    count: int


class MatchBasisMixResponse(BaseModel):
    severity: str | None
    total_links: int
    by_basis: list[MatchBasisStat]
    confidence_bands: list[ConfidenceBandStat]


@router.get("/vuln/match-basis", response_model=MatchBasisMixResponse)
def get_match_basis(
    severity: str | None = None,
    db: Session = Depends(get_session),
) -> MatchBasisMixResponse:
    result = get_match_basis_mix(db, severity)
    return MatchBasisMixResponse.model_validate(result)