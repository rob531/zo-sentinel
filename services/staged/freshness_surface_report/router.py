"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_report


router = APIRouter(prefix="/api", tags=["freshness_surface_report"])


class SurfaceEntry(BaseModel):
    surface: str
    computed_at: datetime
    age_seconds: int
    compute_seconds: int
    status: str
    payload_keys: List[str]


class FreshnessReportResponse(BaseModel):
    surfaces: List[SurfaceEntry]
    stale_count: int
    ok_count: int
    generated_at: datetime


@router.get("/freshness/surfaces", response_model=FreshnessReportResponse)
def get_freshness_surfaces(db: Session = Depends(get_session)) -> FreshnessReportResponse:
    report = get_freshness_report(db)
    return FreshnessReportResponse(**report)