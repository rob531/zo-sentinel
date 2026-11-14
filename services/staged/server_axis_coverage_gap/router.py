"""HTTP surface for the server axis coverage-gap service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_coverage_gap

router = APIRouter(prefix="/api", tags=["scoring"])


class CoverageGapItem(BaseModel):
    server_id: str
    name: str
    registry_source: str | None = None
    risk_tier: str | None = None
    scored_axis_count: int
    missing_axes: list[str]
    stale_axes: list[str]
    last_scored_at: str | None = None


class CoverageGapResponse(BaseModel):
    generated_at: str
    stale_days: int
    total_servers: int
    gap_count: int
    gaps: list[CoverageGapItem]


@router.get("/scoring/coverage-gap", response_model=CoverageGapResponse)
def get_coverage_gap(
    db: Session = Depends(get_session),
    stale_days: int = Query(30, ge=0),
    limit: int = Query(100, ge=1, le=500),
    risk_tier: str | None = Query(None),
) -> CoverageGapResponse:
    result = compute_coverage_gap(db, stale_days=stale_days)
    gaps = list(result.get("gaps", []))

    if risk_tier is not None:
        gaps = [gap for gap in gaps if gap.get("risk_tier") == risk_tier]

    gap_count = len(gaps)
    gaps = gaps[:limit]

    return CoverageGapResponse(
        generated_at=result["generated_at"],
        stale_days=result["stale_days"],
        total_servers=result["total_servers"],
        gap_count=gap_count,
        gaps=gaps,
    )