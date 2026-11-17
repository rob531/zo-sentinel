"""router.py -- HTTP surface for the perspective_unseen_changes service.

Thin wrapper that injects a real DB session via Depends(get_session) and
delegates the core logic to ``services.staged.perspective_unseen_changes.logic``.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# The service logic lives in the sibling ``logic.py`` module.
# It must provide a callable with the signature:
#   get_unseen_changes(db: Session, perspective_id: int, limit: int) -> dict
from .logic import get_unseen_changes

router = APIRouter(prefix="/api/perspectives", tags=["perspective_unseen_changes"])


class EventResponse(BaseModel):
    id: int
    server_id: int
    change_type: str
    old_tier: int | None = None
    new_tier: int | None = None
    created_at: str  # ISO‑8601 timestamp


class PerspectiveUnseenResponse(BaseModel):
    perspective_id: int
    perspective_name: str
    unseen_count: int
    events: List[EventResponse]


@router.get(
    "/{perspective_id}/unseen",
    response_model=PerspectiveUnseenResponse,
    responses={404: {"description": "Perspective not found"}},
)
def list_unseen_changes(
    perspective_id: int,
    limit: int = Query(100, ge=1),
    db: Session = Depends(get_session),
) -> PerspectiveUnseenResponse:
    """
    Return unseen perspective tier‑change events for a given perspective.

    - ``limit`` caps the number of events returned (default 100).
    - Raises 404 if the perspective does not exist.
    """
    result = get_unseen_changes(db, perspective_id, limit)
    if result is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return PerspectiveUnseenResponse(**result)