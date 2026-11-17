"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_overall_risk_timeline

router = APIRouter(prefix="/api", tags=["overall_risk_timeline"])


class SeriesItem(BaseModel):
    scored_at: str = Field(..., description="ISO8601 timestamp")
    p_top: float
    p_critical: float
    p_danger: float
    tier: str
    rule_version: str


class OverallRiskTimelineResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    days: int
    criteria_versions: List[str]
    series: List[SeriesItem]
    unscored: bool


@router.get(
    "/servers/{server_id}/risk/timeline",
    response_model=OverallRiskTimelineResponse,
)
def get_timeline(
    server_id: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> OverallRiskTimelineResponse:
    """
    Return a per‑server overall‑risk timeline.

    Parameters
    ----------
    server_id: str
        Identifier of the server.
    days: int, optional
        Number of days of history to include (default 30, max 365).
    db: Session
        SQLAlchemy session injected by FastAPI.
    """
    return get_overall_risk_timeline(db, server_id, days)