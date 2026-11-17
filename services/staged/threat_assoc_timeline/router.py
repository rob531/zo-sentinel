"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import threat_assoc_timeline

router = APIRouter(prefix="/api/threat", tags=["threat"])


class TimelinePoint(BaseModel):
    date: str
    band: str
    count: int


class ThreatTimelineResponse(BaseModel):
    days: int
    series: list[TimelinePoint]


@router.get("/timeline", response_model=ThreatTimelineResponse)
def get_threat_timeline(
    db: Session = Depends(get_session),
    days: int = Query(30, ge=1),
) -> ThreatTimelineResponse:
    result = threat_assoc_timeline(db, days=days)
    return ThreatTimelineResponse(**result)