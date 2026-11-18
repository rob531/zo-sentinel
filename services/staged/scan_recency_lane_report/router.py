"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_scan_recency_lane_report
from .contract import ScanRecencyLaneResponse

router = APIRouter(prefix="/api", tags=["scan_recency_lane_report"])


@router.get("/registry/scan-recency", response_model=ScanRecencyLaneResponse)
def get_scan_recency_report(db: Session = Depends(get_session)) -> ScanRecencyLaneResponse:
    """Return a report on how recently each registry source lane was scanned."""
    report_data = get_scan_recency_lane_report(db)
    return ScanRecencyLaneResponse(**report_data)