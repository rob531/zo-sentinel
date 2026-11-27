"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service logic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_scan_coverage_backlog


router = APIRouter(prefix="/api", tags=["scan_coverage_backlog"])


class BySourceItem(BaseModel):
    registry_source: str
    total: int
    never_scanned: int
    stale: int
    coverage_pct: float


class BacklogItem(BaseModel):
    server_id: str
    first_seen: str  # ISO‑8601 datetime string


class CoverageResponse(BaseModel):
    total_servers: int
    never_scanned: int
    stale_scanned: int
    by_source: list[BySourceItem]
    backlog: list[BacklogItem]


@router.get("/scan/coverage/backlog", response_model=CoverageResponse)
def get_coverage(db: Session = Depends(get_session)) -> CoverageResponse:
    data = get_scan_coverage_backlog(db)
    return CoverageResponse(**data)