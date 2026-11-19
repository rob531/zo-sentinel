"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the FastAPI application and delegates coverage queries
 to the service's real data layer.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_coverage as get_coverage_data

router = APIRouter(prefix="/api", tags=["scored-coverage"])


class TierCounts(BaseModel):
    counts: dict[str, int]


class SourceCoverage(BaseModel):
    registry_source: Optional[str]
    servers: int
    scored_servers: int
    unscored_servers: int
    coverage_pct: float
    tier_counts: dict[str, int]


class CoverageTotals(BaseModel):
    servers: int
    scored_servers: int
    unscored_servers: int
    coverage_pct: float


class CoverageResponse(BaseModel):
    totals: CoverageTotals
    sources: list[SourceCoverage]


@router.get("/coverage", response_model=CoverageResponse)
def get_coverage(
    db: Session = Depends(get_session),
    registry_source: Optional[str] = None,
) -> CoverageResponse:
    """Return registry coverage and risk-tier distribution by registry source."""
    result = get_coverage_data(db, registry_source=registry_source)

    if isinstance(result, tuple):
        totals, sources = result
    else:
        totals = result["totals"]
        sources = result["sources"]

    return CoverageResponse(
        totals=CoverageTotals(
            servers=int(totals.get("servers", 0)),
            scored_servers=int(totals.get("scored_servers", 0)),
            unscored_servers=int(totals.get("unscored_servers", 0)),
            coverage_pct=round(float(totals.get("coverage_pct", 0.0)), 2),
        ),
        sources=[
            SourceCoverage(
                registry_source=row.get("registry_source"),
                servers=int(row.get("servers", 0)),
                scored_servers=int(row.get("scored_servers", 0)),
                unscored_servers=int(row.get("unscored_servers", 0)),
                coverage_pct=round(float(row.get("coverage_pct", 0.0)), 2),
                tier_counts={
                    str(tier): int(count)
                    for tier, count in (row.get("tier_counts") or {}).items()
                },
            )
            for row in sources
        ],
    )