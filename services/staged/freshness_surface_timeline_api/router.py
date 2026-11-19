"""HTTP surface for freshness surface-timeline data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

from .logic import get_surface_timeline

router = APIRouter(prefix="/api/freshness", tags=["freshness"])


class SurfaceTimelinePoint(BaseModel):
    computed_at: str
    compute_seconds: float
    gap_seconds: float


class SurfaceTimelineEntry(BaseModel):
    surface: str
    points: list[SurfaceTimelinePoint]
    stale: bool


class SurfaceTimelineResponse(BaseModel):
    days: int
    surfaces: list[SurfaceTimelineEntry]


@router.get("/surface-timeline", response_model=SurfaceTimelineResponse)
def get_surface_timeline_response(
    days: int = Query(default=7, ge=1),
    stale_hours: float = Query(default=24.0, ge=0),
    db: Session = Depends(get_session),
) -> SurfaceTimelineResponse:
    timeline = get_surface_timeline(db, days=days, stale_hours=stale_hours)
    surfaces = timeline["surfaces"] if isinstance(timeline, dict) else timeline
    return SurfaceTimelineResponse(days=days, surfaces=surfaces)