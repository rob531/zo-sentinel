"""HTTP surface for vulnerability links whose servers are no longer registered."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_orphan_links

router = APIRouter(prefix="/api", tags=["vulnerability"])


class OrphanLink(BaseModel):
    server_id: str
    link_count: int
    advisory_count: int
    max_match_confidence: float
    severities: list[str]
    latest_linked_at: datetime | None


class OrphansResponse(BaseModel):
    count: int
    orphans: list[OrphanLink]


@router.get("/vuln/links/orphans", response_model=OrphansResponse)
def get_orphans(
    db: Session = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
) -> OrphansResponse:
    rows: list[Any] = get_orphan_links(
        db,
        limit=limit,
        min_confidence=min_confidence,
    )
    orphans = [OrphanLink.model_validate(row) for row in rows]
    return OrphansResponse(count=len(orphans), orphans=orphans)