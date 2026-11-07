"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_surfaces, get_freshness_surface_detail

router = APIRouter(prefix="/api", tags=["freshness"])


class SurfaceDetail(BaseModel):
    surface: str
    computed_at: Optional[datetime] = None
    compute_seconds: Optional[float] = None
    payload: Dict = {}


class SurfacesResponse(BaseModel):
    surfaces: List[SurfaceDetail]


@router.get("/freshness/surfaces", response_model=SurfacesResponse)
def get_surfaces(db: Session = Depends(get_session)) -> SurfacesResponse:
    surfaces = get_freshness_surfaces(db)
    return SurfacesResponse(surfaces=surfaces)


@router.get("/freshness/surfaces/{surface}", response_model=SurfaceDetail)
def get_surface_detail(surface: str, db: Session = Depends(get_session)) -> SurfaceDetail:
    detail = get_freshness_surface_detail(db, surface)
    return detail