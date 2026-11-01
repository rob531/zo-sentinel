"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_key_drift_stats

router = APIRouter(prefix="/api", tags=["freshness"])


class KeyDriftStatsResponse(BaseModel):
    surfaces: list[dict]
    stale_surfaces: list[str]


@router.get("/freshness/key-drift", response_model=KeyDriftStatsResponse)
def get_key_drift_stats(db: Session = Depends(get_session)) -> KeyDriftStatsResponse:
    stats = get_key_drift_stats(db)
    return KeyDriftStatsResponse(surfaces=stats["surfaces"], stale_surfaces=stats["stale_surfaces"])