from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_recent_vuln_advisories

router = APIRouter(prefix="/api", tags=["vuln-advisories"])


class AdvisoryItem(BaseModel):
    id: int
    feed: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    alias_count: int


class RecentAdvisoriesResponse(BaseModel):
    count: int
    items: list[AdvisoryItem]


@router.get("/vuln/advisories/recent", response_model=RecentAdvisoriesResponse)
def get_recent_advisories(
    limit: int = Query(20),
    severity: Optional[str] = Query(None),
    ecosystem: Optional[str] = Query(None),
    days: Optional[int] = Query(None),
    db: Session = Depends(get_session),
) -> RecentAdvisoriesResponse:
    items = get_recent_vuln_advisories(
        db=db,
        limit=limit,
        severity=severity,
        ecosystem=ecosystem,
        days=days,
    )
    return RecentAdvisoriesResponse(count=len(items), items=items)