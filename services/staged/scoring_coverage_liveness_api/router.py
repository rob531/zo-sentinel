from __future__ import annotations

from datetime import datetime
from inspect import isawaitable
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_scoring_coverage_liveness as _compute_coverage_liveness
except ImportError:
    from .logic import compute_scoring_coverage_liveness as _compute_coverage_liveness


class SourceCoverage(BaseModel):
    registry_source: Optional[str] = None
    total: int = 0
    scored: int = 0
    never_scored: int = 0
    coverage_pct: float = 0.0


class ScoringCoverageLivenessResponse(BaseModel):
    total_servers: int = 0
    scored: int = 0
    never_scored: int = 0
    stale: int = 0
    coverage_pct: float = 0.0
    median_score_age_days: Optional[float] = None
    mean_score_age_days: Optional[float] = None
    by_source: List[SourceCoverage] = Field(default_factory=list)
    generated_at: datetime


router = APIRouter(prefix="/api", tags=["scoring"])


@router.get(
    "/scoring/coverage",
    response_model=ScoringCoverageLivenessResponse,
    summary="Scoring coverage and score-age liveness",
)
async def scoring_coverage(
    registry_source: Optional[str] = Query(
        None,
        description="Optional registry-source filter.",
    ),
    days: int = Query(
        30,
        ge=1,
        le=3650,
        description="Freshness window in days.",
    ),
    session: Session = Depends(get_session),
) -> ScoringCoverageLivenessResponse:
    result: Any = _compute_coverage_liveness(
        session=session,
        registry_source=registry_source,
        days=days,
    )
    if isawaitable(result):
        result = await result
    return ScoringCoverageLivenessResponse.model_validate(result)


if __name__ == "__main__":
    print("PASS")