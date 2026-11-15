"""router.py -- thin HTTP surface for the registry_verdict_coverage service.

Exposes `router` (mounted under the main FastAPI app). Delegates all
business logic to `services.staged.registry_verdict_coverage.logic`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_verdict_coverage

router = APIRouter(prefix="/api", tags=["registry_verdict_coverage"])


class VerdictCoverageResponse(BaseModel):
    generated_at: str
    total_servers: int
    unassessed_count: int
    never_scanned_count: int
    stale_assessed_count: int
    by_verdict: dict[str, int]
    by_risk_tier: dict[str, int]


@router.get(
    "/registry/verdict-coverage",
    response_model=VerdictCoverageResponse,
    summary="Coverage of verdicts and risk tiers across the server registry",
)
def verdict_coverage_endpoint(db: Session = Depends(get_session)) -> VerdictCoverageResponse:
    """Return a summary of verdict and risk‑tier distribution for all servers."""
    result = get_verdict_coverage(db)
    return VerdictCoverageResponse(**result)