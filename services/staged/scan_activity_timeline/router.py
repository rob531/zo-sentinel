"""HTTP surface for the scan activity timeline service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import scan_activity_timeline

router = APIRouter(prefix="/api/scanning", tags=["scanning"])


class TimelinePoint(BaseModel):
    date: str
    scanned: int
    scan_count_sum: int


class TimelineSource(BaseModel):
    registry_source: str
    servers: int


class TimelineResponse(BaseModel):
    days: int
    series: list[TimelinePoint]
    sources: list[TimelineSource]
    last_scan_at: str | None


@router.get("/timeline", response_model=TimelineResponse)
def get_scan_activity_timeline(
    db: Session = Depends(get_session),
    days: int = Query(default=14),
) -> TimelineResponse:
    days = max(1, min(90, days))
    result = scan_activity_timeline(db, days)
    return TimelineResponse(**result)