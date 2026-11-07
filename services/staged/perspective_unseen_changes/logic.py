"""logic.py -- the service's data/computation layer (one concern, one file).

Provides read-only access to unseen perspective tier-change events using the
real application database session and ORM models.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Perspective, PerspectiveEvent


def get_unseen_perspective_events(
    db: Session, perspective_id: Any, limit: int = 100
) -> Dict[str, Any]:
    """Return unseen tier-change events for a perspective.

    The perspective is checked first so an unknown identifier produces a
    consistent 404 JSON error.  The query only reads application tables and
    never acknowledges an event.
    """
    if limit < 1:
        raise HTTPException(status_code=400, detail={"error": "limit must be positive"})

    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if perspective is None:
        raise HTTPException(
            status_code=404, detail={"error": "Perspective not found"}
        )

    unseen_count = int(
        db.execute(
            select(func.count(PerspectiveEvent.id)).where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.seen.is_(False),
            )
        ).scalar_one()
    )

    events = db.execute(
        select(PerspectiveEvent)
        .where(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen.is_(False),
        )
        .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
        .limit(limit)
    ).scalars().all()

    return {
        "perspective_id": perspective.id,
        "perspective_name": perspective.name,
        "unseen_count": unseen_count,
        "events": [
            {
                "id": event.id,
                "server_id": event.server_id,
                "change_type": event.change_type,
                "old_tier": event.old_tier,
                "new_tier": event.new_tier,
                "created_at": event.created_at,
                "seen": event.seen,
            }
            for event in events
        ],
    }


# Compatibility names used by staged routers.
get_unseen_events = get_unseen_perspective_events
list_unseen_events = get_unseen_perspective_events
get_unseen_changes = get_unseen_perspective_events


if __name__ == "__main__":
    print("PASS")