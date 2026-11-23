"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Delegates to logic, injecting the real
SQLAlchemy Session via Depends(get_session). Relative imports keep the
module usable in both staged and active service trees.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# logic returns a plain dict matching the response model below
from .logic import get_match_confidence_report

router = APIRouter(prefix="/api", tags=["cve_match_confidence"])


class Bucket(BaseModel):
    match_basis: str
    severity: Optional[str] = None
    min_confidence: float
    max_confidence: float
    avg_confidence: float
    link_count: int


class LowConfidenceLink(BaseModel):
    link_id: int
    server_id: int
    server_name: str
    advisory_id: int
    package: Optional[str] = None
    match_basis: str
    match_value: str
    match_confidence: float


class MatchConfidenceResponse(BaseModel):
    buckets: List[Bucket]
    total_links: int
    low_confidence_links: List[LowConfidenceLink]


@router.get(
    "/vuln/cve/match-confidence",
    response_model=MatchConfidenceResponse,
    summary="Confidence distribution for CVE‑to‑server linkages",
)
def get_match_confidence(
    min_confidence: Optional[float] = Query(
        None, ge=0.0, le=1.0, description="Minimum confidence to include in low‑confidence list"
    ),
    limit: Optional[int] = Query(
        None, ge=1, le=500, description="Maximum number of low‑confidence links to return"
    ),
    db: Session = Depends(get_session),
) -> MatchConfidenceResponse:
    """Return bucketed confidence statistics and optionally low‑confidence links."""
    payload = get_match_confidence_report(db, min_confidence=min_confidence, limit=limit)
    return MatchConfidenceResponse(**payload)