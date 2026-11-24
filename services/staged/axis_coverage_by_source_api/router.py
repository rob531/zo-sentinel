"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_coverage_by_source

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class AxisCoverage(BaseModel):
    axis_name: str
    servers_scored: int
    avg_p_top: float | None
    missing_servers: int


class SourceCoverage(BaseModel):
    registry_source: str
    axes: list[AxisCoverage]


class CoverageTotals(BaseModel):
    sources: int
    axes: int


class AxisCoverageResponse(BaseModel):
    sources: list[SourceCoverage]
    totals: CoverageTotals


@router.get(
    "/axis-coverage-by-source",
    response_model=AxisCoverageResponse,
)
def get_axis_coverage_by_source(
    db: Session = Depends(get_session),
) -> AxisCoverageResponse:
    result = axis_coverage_by_source(db)
    return AxisCoverageResponse(
        sources=[
            SourceCoverage(
                registry_source=source["registry_source"],
                axes=[
                    AxisCoverage(
                        axis_name=axis["axis_name"],
                        servers_scored=axis["servers_scored"],
                        avg_p_top=axis["avg_p_top"],
                        missing_servers=axis["missing_servers"],
                    )
                    for axis in source["axes"]
                ],
            )
            for source in result["sources"]
        ],
        totals=CoverageTotals(
            sources=result["totals"]["sources"],
            axes=result["totals"]["axes"],
        ),
    )