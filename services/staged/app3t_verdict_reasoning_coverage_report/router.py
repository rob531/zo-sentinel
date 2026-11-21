"""HTTP surface for the verdict-reasoning coverage report."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import verdict_reasoning_coverage

router = APIRouter(prefix="/api", tags=["verdict-reasoning-coverage"])


class VerdictCoverage(BaseModel):
    verdict: str
    servers: int
    with_reasoning: int
    reasoning_coverage_pct: float
    avg_confidence: float | None
    avg_reasoning_chars: float | None


class CoverageTotals(BaseModel):
    servers: int
    verdicts: int
    with_reasoning: int


class VerdictReasoningCoverageResponse(BaseModel):
    verdicts: list[VerdictCoverage]
    totals: CoverageTotals


@router.get(
    "/report/verdict-reasoning-coverage",
    response_model=VerdictReasoningCoverageResponse,
)
def get_verdict_reasoning_coverage(
    db: Session = Depends(get_session),
) -> VerdictReasoningCoverageResponse:
    return verdict_reasoning_coverage(db)