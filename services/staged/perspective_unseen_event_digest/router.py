from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unseen_event_digest

router = APIRouter(prefix="/api", tags=["perspective_unseen_event_digest"])


class UnseenEventResponse(BaseModel):
    event_id: str | int
    server_id: str | int
    old_tier: str | None
    new_tier: str | None
    change_type: str
    created_at: str


class UnseenEventsResponse(BaseModel):
    perspective_id: str
    perspective_name: str
    unseen_count: int
    events: list[UnseenEventResponse]


@router.get(
    "/perspectives/{perspective_id}/unseen-events",
    response_model=UnseenEventsResponse,
)
def get_unseen_events(
    perspective_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> UnseenEventsResponse:
    digest = get_unseen_event_digest(
        db,
        perspective_id=perspective_id,
        limit=limit,
    )
    return UnseenEventsResponse(
        perspective_id=str(digest["perspective_id"]),
        perspective_name=digest["perspective_name"],
        unseen_count=digest["unseen_count"],
        events=digest["events"],
    )