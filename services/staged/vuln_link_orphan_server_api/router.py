"""HTTP surface for orphaned vulnerability-link discovery."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_orphan_links

router = APIRouter(prefix="/api/vuln/links", tags=["vuln-links"])


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


@router.get("/orphans", response_model=OrphansResponse)
def get_orphans(
    limit: int = Query(default=100, ge=1, le=500),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    db: Session = Depends(get_session),
) -> OrphansResponse:
    result: Any = get_orphan_links(
        db,
        limit=limit,
        min_confidence=min_confidence,
    )

    if isinstance(result, dict):
        rows = result.get("orphans", [])
        count = int(result.get("count", len(rows)))
    elif isinstance(result, tuple):
        rows = result[1]
        count = int(result[0])
    else:
        rows = result
        count = len(rows)

    orphans = [
        row if isinstance(row, OrphanLink) else OrphanLink.parse_obj(row)
        for row in rows
    ]
    return OrphansResponse(count=count, orphans=orphans)