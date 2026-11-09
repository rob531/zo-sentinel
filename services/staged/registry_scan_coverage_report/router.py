"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# The logic function that builds the report.
# It returns a dict matching the ReportResponse schema.
from .logic import registry_scan_coverage_report


router = APIRouter(prefix="/api", tags=["registry_scan_coverage_report"])


class SourceSummary(BaseModel):
    registry_source: str
    total: int
    scanned_ever: int
    never_scanned: int
    stale: int
    median_scan_count: float
    oldest_last_scanned: Optional[datetime] = None
    newest_last_scanned: Optional[datetime] = None
    by_risk_tier: Dict[str, int]


class ReportResponse(BaseModel):
    days: int
    generated_at: datetime
    sources: List[SourceSummary]


@router.get(
    "/registry/scan-coverage",
    response_model=ReportResponse,
    summary="Coverage report for registry scans",
)
def get_registry_scan_coverage(
    days: int = Query(30, ge=1, description="Number of days to consider a scan stale"),
    db: Session = Depends(get_session),
) -> ReportResponse:
    """
    Return a coverage summary per registry source.

    The heavy lifting is performed by `registry_scan_coverage_report` in
    `logic.py`. This endpoint merely validates the query parameter,
    injects the DB session, and shapes the response.
    """
    raw = registry_scan_coverage_report(db, days)
    return ReportResponse(**raw)