"""HTTP surface for registry scan coverage reporting."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_scan_coverage

router = APIRouter(prefix="/api/registry", tags=["registry-scan-coverage"])


class ScanCoverageSource(BaseModel):
    registry_source: str
    total: int
    scanned: int
    stale: int
    never_scanned: int
    mean_scan_count: float
    by_verdict: dict[str, int]
    by_risk_tier: dict[str, int]


class ScanCoverageResponse(BaseModel):
    generated_at: datetime
    days: int
    sources: list[ScanCoverageSource]


@router.get("/scan-coverage", response_model=ScanCoverageResponse)
def get_registry_scan_coverage(
    db: Session = Depends(get_session),
    registry_source: str | None = Query(default=None),
    days: int = Query(default=30, ge=1),
) -> ScanCoverageResponse:
    rows: list[dict[str, Any]] = get_scan_coverage(
        db,
        registry_source=registry_source,
        days=days,
    )
    return ScanCoverageResponse(
        generated_at=datetime.now(timezone.utc),
        days=days,
        sources=[ScanCoverageSource(**row) for row in rows],
    )