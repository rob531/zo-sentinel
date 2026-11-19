"""Logic for the perspective tier-change activity feed."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Perspective, PerspectiveEvent

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _normalize_limit(limit: int) -> int:
    return max(0, min(int(limit), MAX_LIMIT))


def _timestamp(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_perspective_activity(
    db: Session, limit: int = DEFAULT_LIMIT
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return the latest labeled perspective events and tier-transition counts."""
    normalized_limit = _normalize_limit(limit)

    event_rows = db.execute(
        select(
            PerspectiveEvent.id,
            PerspectiveEvent.perspective_id,
            Perspective.name.label("perspective_name"),
            PerspectiveEvent.server_id,
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.seen,
            PerspectiveEvent.created_at,
        )
        .select_from(PerspectiveEvent)
        .outerjoin(Perspective, Perspective.id == PerspectiveEvent.perspective_id)
        .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
        .limit(normalized_limit)
    ).all()

    events: List[Dict[str, Any]] = [
        {
            "id": int(row.id),
            "perspective_id": str(row.perspective_id),
            "perspective_name": row.perspective_name,
            "server_id": str(row.server_id),
            "change_type": str(row.change_type),
            "old_tier": row.old_tier,
            "new_tier": row.new_tier,
            "seen": bool(row.seen),
            "created_at": _timestamp(row.created_at),
        }
        for row in event_rows
    ]

    transition_rows = db.execute(
        select(
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            func.count(PerspectiveEvent.id),
        )
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .order_by(
            func.count(PerspectiveEvent.id).desc(),
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
        )
    ).all()

    transitions: List[Dict[str, Any]] = [
        {
            "old_tier": row.old_tier,
            "new_tier": row.new_tier,
            "count": int(row[2]),
        }
        for row in transition_rows
    ]

    return events, transitions


def list_perspective_activity(
    db: Session, limit: int = DEFAULT_LIMIT
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Compatibility wrapper for callers that use list-style naming."""
    return get_perspective_activity(db, limit)


def get_activity_events(
    db: Session, limit: int = DEFAULT_LIMIT
) -> List[Dict[str, Any]]:
    """Return only the labeled activity events."""
    return get_perspective_activity(db, limit)[0]


def get_transition_counts(db: Session) -> List[Dict[str, Any]]:
    """Return counts grouped by old-tier to new-tier transitions."""
    return get_perspective_activity(db, 0)[1]


def get_perspective_change_counts(db: Session) -> List[Dict[str, Any]]:
    """Return event counts grouped by perspective and change type."""
    rows = db.execute(
        select(
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.change_type,
            func.count(PerspectiveEvent.id),
        )
        .group_by(PerspectiveEvent.perspective_id, PerspectiveEvent.change_type)
        .order_by(
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.change_type,
        )
    ).all()

    return [
        {
            "perspective_id": str(row.perspective_id),
            "change_type": str(row.change_type),
            "count": int(row[2]),
        }
        for row in rows
    ]


def get_change_type_counts(db: Session) -> List[Dict[str, Any]]:
    """Compatibility wrapper for perspective/change-type aggregation."""
    return get_perspective_change_counts(db)