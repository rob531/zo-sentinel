"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for service mounting. Thin: injects the real Session via
Depends(get_session), delegates all computation to logic.py.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_linkage_gaps

router = APIRouter(prefix="/api", tags=["vuln"])


class LinkageGapBucket(BaseModel):
    tier: str
    servers_total: int
    servers_with_links: int
    servers_without_links: int
    pct_unlinked: float


class UnlinkedAdvisory(BaseModel):
    advisory_id: int
    severity: str
    published_at: datetime | None


class LinkageGapTotals(BaseModel):
    servers: int
    servers_without_links: int


class LinkageGapResponse(BaseModel):
    generated_at: datetime
    buckets: list[LinkageGapBucket]
    unlinked_advisories: list[UnlinkedAdvisory]
    totals: LinkageGapTotals


@router.get("/vuln/linkage-gaps", response_model=LinkageGapResponse)
def get_linkage_gaps(db: Session = Depends(get_session)) -> LinkageGapResponse:
    return compute_linkage_gaps(db)