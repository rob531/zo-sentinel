"""HTTP surface for the registry untiered backlog service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_untiered_backlog

router = APIRouter(prefix="/api/registry", tags=["registry"])


class SourceCount(BaseModel):
    registry_source: str | None = None
    count: int


class StaleUnscoredServer(BaseModel):
    server_id: str
    name: str | None = None
    registry_source: str | None = None
    last_assessed: datetime | None = None
    age_days: int


class UntieredBacklogResponse(BaseModel):
    days: int
    total_untiered: int
    by_source: list[SourceCount]
    stale_unscored: list[StaleUnscoredServer]
    oldest_age_days: int


@router.get("/untiered-backlog", response_model=UntieredBacklogResponse)
def get_untiered_backlog_endpoint(
    days: int = 30,
    db: Session = Depends(get_session),
) -> UntieredBacklogResponse:
    return get_untiered_backlog(db, days)