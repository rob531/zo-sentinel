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
from app.models import FreshnessStat

from .logic import get_freshness_surface_detail

router = APIRouter(prefix="/api/freshness_surface_detail", tags=["freshness_surface_detail"])


class FreshnessSurfaceDetailResponse(BaseModel):
    compute_seconds: float
    computed_at: str
    payload: str
    surface: str


@router.get("/{surface}", response_model=FreshnessSurfaceDetailResponse)
def get_freshness_surface_detail(surface: str, db: Session = Depends(get_session)) -> FreshnessSurfaceDetailResponse:
    freshness_stat = get_freshness_surface_detail(db, surface)
    return FreshnessSurfaceDetailResponse(
        compute_seconds=freshness_stat.compute_seconds,
        computed_at=str(freshness_stat.computed_at),
        payload=freshness_stat.payload,
        surface=freshness_stat.surface
    )