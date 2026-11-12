"""router.py -- HTTP surface for registry verdict coverage.

Thin wrapper that injects a real DB session via Depends(get_session) and
delegates to the service's logic layer.
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


@router.get("/registry/verdict-coverage", response_model=VerdictCoverageResponse)
def verdict_coverage_endpoint(db: Session = Depends(get_session)) -> VerdictCoverageResponse:
    data = get_verdict_coverage(db)
    return VerdictCoverageResponse(**data)