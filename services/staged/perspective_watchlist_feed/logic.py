"""Logic for the perspective watchlist feed."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, Perspective, PerspectiveEvent


def get_perspective_watchlist(
    db: Session,
    perspective_id: int,
    unseen_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return a perspective and its enriched, most-recent watchlist events."""
    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if perspective is None:
        return None

    total_events = int(
        db.execute(
            select(func.count(PerspectiveEvent.id)).where(
                PerspectiveEvent.perspective_id == perspective_id
            )
        ).scalar_one()
    )

    unseen_count = int(
        db.execute(
            select(func.count(PerspectiveEvent.id)).where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.seen.is_(False),
            )
        ).scalar_one()
    )

    event_query = (
        select(PerspectiveEvent, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == PerspectiveEvent.server_id,
        )
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
        .limit(200)
    )
    if unseen_only:
        event_query = event_query.where(PerspectiveEvent.seen.is_(False))

    rows = db.execute(event_query).all()
    events: List[Dict[str, Any]] = []
    for event, registry in rows:
        events.append(
            {
                "event_id": event.id,
                "server_id": event.server_id,
                "server_name": registry.name if registry is not None else None,
                "current_risk_tier": (
                    registry.risk_tier if registry is not None else None
                ),
                "change_type": event.change_type,
                "old_tier": event.old_tier,
                "new_tier": event.new_tier,
                "seen": bool(event.seen),
                "created_at": event.created_at,
            }
        )

    return {
        "perspective": {
            "id": perspective.id,
            "name": perspective.name,
            "description": perspective.description,
            "org_id": perspective.org_id,
        },
        "total_events": total_events,
        "unseen_count": unseen_count,
        "events": events,
    }


def get_watchlist(
    db: Session,
    perspective_id: int,
    unseen_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """Compatibility alias for the watchlist feed query."""
    return get_perspective_watchlist(db, perspective_id, unseen_only)


def get_perspective_watchlist_feed(
    db: Session,
    perspective_id: int,
    unseen_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """Compatibility alias for the watchlist feed query."""
    return get_perspective_watchlist(db, perspective_id, unseen_only)