"""HTTP surface for the perspective watchlist API."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_perspective_watchlist

router = APIRouter(prefix="/api", tags=["perspective-watchlist"])


class WatchlistMemberResponse(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    last_assessed: datetime | None = None


class PerspectiveWatchlistResponse(BaseModel):
    perspective_id: int
    name: str
    description: str | None = None
    org_id: str | None = None
    taken_at: datetime | None = None
    member_count: int
    tier_counts: dict[str, int]
    members: list[WatchlistMemberResponse]


@router.get(
    "/perspectives/{perspective_id}/watchlist",
    response_model=PerspectiveWatchlistResponse,
)
def get_watchlist(
    perspective_id: int,
    db: Session = Depends(get_session),
) -> PerspectiveWatchlistResponse:
    payload = get_perspective_watchlist(db, perspective_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return PerspectiveWatchlistResponse(**payload)