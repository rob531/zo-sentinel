"""router.py -- the perspective change stream HTTP surface.

Exposes `router` for mounting by the service spine. Reads only the
application database and returns per-perspective change activity and
membership history.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

try:
    from .logic import get_change_stream as _get_change_stream
except ImportError:
    _get_change_stream = None


router = APIRouter(prefix="/api", tags=["perspective-change-stream"])


class TransitionResponse(BaseModel):
    old_tier: str | None
    new_tier: str | None
    count: int


class SeriesPointResponse(BaseModel):
    date: str
    count: int


class LatestSnapshotResponse(BaseModel):
    taken_at: datetime | None
    membership_size: int


class PerspectiveChangeStreamResponse(BaseModel):
    perspective_id: str
    name: str | None
    days: int
    total_events: int
    distinct_servers: int
    by_change_type: dict[str, int]
    transitions: list[TransitionResponse]
    series: list[SeriesPointResponse]
    latest_snapshot: LatestSnapshotResponse
    unseen_count: int


def _membership_size(membership: Any) -> int:
    """Return a safe size for an opaque membership JSON value."""
    if isinstance(membership, (list, tuple, set)):
        return len(membership)
    if isinstance(membership, dict):
        for key in ("servers", "members", "server_ids", "ids"):
            value = membership.get(key)
            if isinstance(value, (list, tuple, set)):
                return len(value)
        return len(membership)
    return 0


def _as_iso_date(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date().isoformat()


def _load_change_stream(
    db: Session,
    perspective_id: str,
    days: int,
    include_unseen: bool,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    perspective = db.get(Perspective, perspective_id)
    events = list(
        db.scalars(
            select(PerspectiveEvent)
            .where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.created_at >= cutoff,
            )
            .order_by(PerspectiveEvent.created_at.asc(), PerspectiveEvent.id.asc())
        )
    )

    snapshot = db.scalar(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
        .limit(1)
    )

    by_change_type = Counter(event.change_type for event in events)
    transition_counts = Counter(
        (event.old_tier, event.new_tier) for event in events
    )
    day_counts = Counter(_as_iso_date(event.created_at) for event in events)

    return {
        "perspective_id": perspective_id,
        "name": perspective.name if perspective is not None else None,
        "days": days,
        "total_events": len(events),
        "distinct_servers": len(
            {event.server_id for event in events if event.server_id is not None}
        ),
        "by_change_type": dict(by_change_type),
        "transitions": [
            {
                "old_tier": old_tier,
                "new_tier": new_tier,
                "count": count,
            }
            for (old_tier, new_tier), count in transition_counts.items()
        ],
        "series": [
            {"date": date, "count": count}
            for date, count in sorted(day_counts.items())
        ],
        "latest_snapshot": {
            "taken_at": snapshot.taken_at if snapshot is not None else None,
            "membership_size": (
                _membership_size(snapshot.membership)
                if snapshot is not None
                else 0
            ),
        },
        "unseen_count": sum(1 for event in events if not event.seen),
    }


@router.get(
    "/perspectives/{perspective_id}/changes",
    response_model=PerspectiveChangeStreamResponse,
)
def get_changes(
    perspective_id: str,
    days: int = Query(default=14, ge=1),
    include_unseen: bool = Query(default=False),
    db: Session = Depends(get_session),
) -> PerspectiveChangeStreamResponse:
    result = _load_change_stream(db, perspective_id, days, include_unseen)
    return PerspectiveChangeStreamResponse(**result)