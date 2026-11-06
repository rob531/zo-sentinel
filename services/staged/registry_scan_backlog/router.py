"""HTTP surface for the registry scan-backlog service."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import scan_backlog_summary

router = APIRouter(prefix="/api/registry", tags=["registry-scan-backlog"])


class ScanBacklogTotals(BaseModel):
    registry_servers: int
    never_scanned: int
    backlog_over_7d: int


class ScanBacklogBucket(BaseModel):
    bucket: str
    count: int
    pct: float


class OldestNeverScannedServer(BaseModel):
    server_id: str
    name: str
    registry_source: Optional[str] = None
    first_seen: Optional[datetime] = None


class ScanBacklogResponse(BaseModel):
    generated_at: datetime
    totals: ScanBacklogTotals
    buckets: list[ScanBacklogBucket]
    oldest_never_scanned: list[OldestNeverScannedServer]


@router.get("/scan-backlog", response_model=ScanBacklogResponse)
def get_scan_backlog(db: Session = Depends(get_session)) -> ScanBacklogResponse:
    return ScanBacklogResponse.model_validate(scan_backlog_summary(db))