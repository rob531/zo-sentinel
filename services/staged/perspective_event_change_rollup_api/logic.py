from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


def get_perspective_event_rollup(
    session: Session,
    perspective_id: Optional[str] = None,
) -> Dict[str, Any]:
    statement = select(
        PerspectiveEvent.id,
        PerspectiveEvent.perspective_id,
        PerspectiveEvent.server_id,
        PerspectiveEvent.change_type,
        PerspectiveEvent.old_tier,
        PerspectiveEvent.new_tier,
        PerspectiveEvent.seen,
        PerspectiveEvent.created_at,
    )
    if perspective_id is not None:
        statement = statement.where(PerspectiveEvent.perspective_id == perspective_id)

    rows = session.execute(statement).all()

    change_type_counts: Counter = Counter()
    transition_counts: Counter = Counter()
    perspective_counts: Counter = Counter()
    recent: List[Dict[str, Any]] = []

    for row in rows:
        change_type_counts[row.change_type] += 1
        transition_counts[
            (
                "none" if row.old_tier is None else row.old_tier,
                "none" if row.new_tier is None else row.new_tier,
            )
        ] += 1
        perspective_counts[row.perspective_id] += 1
        recent.append(
            {
                "id": row.id,
                "perspective_id": row.perspective_id,
                "server_id": row.server_id,
                "change_type": row.change_type,
                "old_tier": row.old_tier,
                "new_tier": row.new_tier,
                "seen": row.seen,
                "created_at": row.created_at,
            }
        )

    recent.sort(
        key=lambda event: (
            event["created_at"] is None,
            event["created_at"] or "",
            event["id"] or 0,
        ),
        reverse=True,
    )

    return {
        "total_events": len(rows),
        "by_change_type": [
            {"change_type": change_type, "count": count}
            for change_type, count in sorted(
                change_type_counts.items(),
                key=lambda item: (-item[1], str(item[0])),
            )
        ],
        "by_transition": [
            {"old_tier": old_tier, "new_tier": new_tier, "count": count}
            for (old_tier, new_tier), count in sorted(
                transition_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
        ],
        "by_perspective": [
            {"perspective_id": perspective, "count": count}
            for perspective, count in sorted(
                perspective_counts.items(),
                key=lambda item: (-item[1], str(item[0])),
            )
        ],
        "recent": recent[:50],
    }


def get_event_rollup(
    session: Session,
    perspective_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_perspective_event_rollup(session, perspective_id)


def perspective_event_rollup_endpoint(
    perspective_id: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_perspective_event_rollup(session, perspective_id)