"""logic.py -- unseen tier-change event digest logic for a perspective."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


def get_unseen_event_digest(
    db: Session,
    perspective_id: str,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return the unseen tier-change events for one perspective.

    The perspective is read from the application database and only events whose
    ``seen`` flag is false are included.  Events are ordered newest first and
    the number of returned rows is capped at 500.
    """
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = 50
    normalized_limit = max(1, min(normalized_limit, 500))

    perspective = db.get(Perspective, perspective_id)
    if perspective is None:
        return {
            "perspective_id": str(perspective_id),
            "perspective_name": "",
            "unseen_count": 0,
            "events": [],
        }

    count_query = select(PerspectiveEvent.id).where(
        PerspectiveEvent.perspective_id == perspective_id,
        PerspectiveEvent.seen.is_(False),
    )
    unseen_count = len(db.execute(count_query).all())

    events_query = (
        select(PerspectiveEvent)
        .where(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen.is_(False),
        )
        .order_by(
            PerspectiveEvent.created_at.desc(),
            PerspectiveEvent.id.desc(),
        )
        .limit(normalized_limit)
    )
    event_rows = db.execute(events_query).scalars().all()

    events: List[Dict[str, Any]] = []
    for event in event_rows:
        events.append(
            {
                "event_id": str(event.id),
                "server_id": str(event.server_id),
                "old_tier": event.old_tier,
                "new_tier": event.new_tier,
                "change_type": event.change_type,
                "created_at": event.created_at,
            }
        )

    return {
        "perspective_id": str(perspective.id),
        "perspective_name": str(perspective.name),
        "unseen_count": int(unseen_count),
        "events": events,
    }


def get_unseen_events(
    db: Session,
    perspective_id: str,
    limit: int = 50,
) -> Dict[str, Any]:
    """Compatibility name for the unseen-event digest operation."""
    return get_unseen_event_digest(db, perspective_id, limit)


def unseen_event_digest(
    db: Session,
    perspective_id: str,
    limit: int = 50,
) -> Dict[str, Any]:
    """Compatibility name for the unseen-event digest operation."""
    return get_unseen_event_digest(db, perspective_id, limit)


__all__ = [
    "get_session",
    "get_unseen_event_digest",
    "get_unseen_events",
    "unseen_event_digest",
]