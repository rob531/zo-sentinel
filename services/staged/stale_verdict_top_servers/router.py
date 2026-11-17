"""HTTP surface for stale registry verdict reporting."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_stale_verdicts

router = APIRouter(prefix="/api", tags=["stale-verdict-top-servers"])


class StaleServer(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    risk_tier: str | None = None
    confidence: float | None = None
    last_assessed: datetime | None = None
    last_seen: datetime | None = None
    scan_count: int = 0
    age_hours: float | None = None


class StaleVerdictResponse(BaseModel):
    threshold_hours: int
    total_stale: int
    stale_servers: list[StaleServer]
    never_assessed_count: int


@router.get("/registry/stale-verdicts", response_model=StaleVerdictResponse)
def get_stale_verdict_top_servers(
    hours: int = Query(default=48, ge=1),
    limit: int = Query(default=50, ge=1),
    db: Session = Depends(get_session),
) -> StaleVerdictResponse:
    result = get_stale_verdicts(db, hours=hours, limit=limit)
    return StaleVerdictResponse(**result)