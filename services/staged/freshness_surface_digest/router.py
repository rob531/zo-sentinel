from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_surface_digest

router = APIRouter(prefix="/api/freshness", tags=["freshness"])


class SurfaceDigestEntry(BaseModel):
    surface: str
    computed_at: str
    compute_seconds: float
    payload_keys: list[str]
    payload_size: int
    stale: bool


class SurfaceDigestResponse(BaseModel):
    surfaces: list[SurfaceDigestEntry]


@router.get("/surfaces", response_model=SurfaceDigestResponse)
def get_surfaces(db: Session = Depends(get_session)) -> SurfaceDigestResponse:
    digest = get_surface_digest(db)
    return SurfaceDigestResponse(surfaces=digest["surfaces"])