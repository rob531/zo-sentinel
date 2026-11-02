"""HTTP surface for threat-intelligence coverage reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import coverage_ratio

router = APIRouter(prefix="/api", tags=["threat-intel"])


class CoverageBucket(BaseModel):
    registry_source: str
    total_servers: int
    covered_servers: int
    coverage_ratio: float
    median_intel_age_days: float | None


class CoverageResponse(BaseModel):
    generated_at: datetime
    buckets: list[CoverageBucket]


@router.get("/threat-intel/coverage", response_model=CoverageResponse)
def get_threat_intel_coverage(
    registry_source: str | None = None,
    min_ratio: float | None = Query(default=None, ge=0.0, le=1.0),
    db: Session = Depends(get_session),
) -> CoverageResponse:
    result: dict[str, Any] = coverage_ratio(
        db,
        registry_source=registry_source,
        min_ratio=min_ratio,
    )
    return CoverageResponse(**result)