"""router.py -- the service's HTTP surface (one concern, one file).

Thin wrapper that injects the real Session via Depends(get_session) and
delegates to the service's logic module.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_resolution_leadtime

router = APIRouter(prefix="/api", tags=["dispute_resolution_leadtime"])


class StatusBucket(BaseModel):
    status: str
    count: int


class ResolvedStats(BaseModel):
    count: int
    mean_hours: float | None
    median_hours: float | None
    p90_hours: float | None
    max_hours: float | None


class LeadtimeResponse(BaseModel):
    status_buckets: list[StatusBucket]
    resolved: ResolvedStats
    generated_at: datetime


@router.get(
    "/disputes/resolution-leadtime",
    response_model=LeadtimeResponse,
    summary="Resolution lead‑time statistics for disputes",
)
def get_resolution_leadtime(db: Session = Depends(get_session)) -> LeadtimeResponse:
    """Return aggregated lead‑time metrics for dispute resolution."""
    payload = compute_resolution_leadtime(db)
    return LeadtimeResponse(**payload)