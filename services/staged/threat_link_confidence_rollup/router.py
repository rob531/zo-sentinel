"""HTTP surface for threat link confidence rollups."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import link_confidence_rollup

router = APIRouter(prefix="/api/vuln", tags=["threat-link-confidence-rollup"])


class BasisConfidenceResponse(BaseModel):
    count: int
    avg_confidence: float
    high_confidence: int


class LinkConfidenceResponse(BaseModel):
    total_links: int
    by_basis: dict[str, BasisConfidenceResponse]
    servers_with_links: int
    advisories_with_links: int
    min_confidence: float


@router.get("/link-confidence", response_model=LinkConfidenceResponse)
def get_link_confidence(
    min_confidence: float = Query(0.0),
    db: Session = Depends(get_session),
) -> LinkConfidenceResponse:
    effective_min_confidence = max(0.0, min(1.0, min_confidence))
    result = link_confidence_rollup(db, effective_min_confidence)
    return LinkConfidenceResponse(**result)