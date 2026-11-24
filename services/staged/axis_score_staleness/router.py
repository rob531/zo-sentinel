"""HTTP surface for SFT axis-score staleness reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_score_staleness

router = APIRouter(prefix="/api/scores", tags=["scores"])


class ServerStaleness(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    last_scored_at: datetime | None = None
    axes_scored: int = 0


class StalenessBucket(BaseModel):
    bucket: str
    count: int
    servers: list[ServerStaleness] = []


class ScoreStalenessResponse(BaseModel):
    max_age_hours: int
    buckets: list[StalenessBucket]
    total: int


@router.get("/staleness", response_model=ScoreStalenessResponse)
def get_axis_score_staleness(
    max_age_hours: int = Query(default=24, gt=0),
    db: Session = Depends(get_session),
) -> ScoreStalenessResponse:
    result: Any = get_score_staleness(db, max_age_hours=max_age_hours)
    return ScoreStalenessResponse.model_validate(result)