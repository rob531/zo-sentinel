"""router.py -- the service's HTTP surface.

Exposes `router` for the registry scan-gap watch service. The route delegates
all data access and scan-gap calculation to `logic.py` using the application
database session.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import scan_gap_watch

router = APIRouter(prefix="/api", tags=["registry-scan-gap-watch"])


class ScanGapItem(BaseModel):
    server_id: str
    name: str
    registry_source: str
    last_seen: str | None
    last_scanned: str | None
    scan_count: int
    days_since_scan: int | None


class SourceCount(BaseModel):
    source: str
    count: int


class ScanGapResponse(BaseModel):
    days: int
    total: int
    stale_count: int
    by_source: list[SourceCount]
    items: list[ScanGapItem]


@router.get("/registry/scan-gaps", response_model=ScanGapResponse)
def get_scan_gaps(
    days: int = 30,
    db: Session = Depends(get_session),
) -> ScanGapResponse:
    result = scan_gap_watch(db, days)
    return ScanGapResponse(
        days=result["days"],
        total=result["total"],
        stale_count=result["stale_count"],
        by_source=[SourceCount(**source) for source in result["by_source"]],
        items=[ScanGapItem(**item) for item in result["items"]],
    )