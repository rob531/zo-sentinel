"""logic.py -- perspective snapshot ageing data and computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _membership_size(membership: Any) -> int:
    """Return the length of a membership JSON array without database JSON operators."""
    if membership is None:
        return 0
    if isinstance(membership, str):
        import json

        try:
            membership = json.loads(membership)
        except (TypeError, ValueError):
            return 0
    if isinstance(membership, list):
        return len(membership)
    return 0


def _is_tier_change(event: PerspectiveEvent) -> bool:
    change_type = str(event.change_type or "").strip().lower()
    if change_type in {"tier_change", "tier_changed", "tier_update"}:
        return True
    return event.old_tier != event.new_tier


def get_snapshot_ageing(db: Session) -> List[Dict[str, Any]]:
    """Compute snapshot freshness and event drift for every perspective."""
    perspectives = list(db.execute(select(Perspective).order_by(Perspective.id)).scalars().all())
    snapshots = list(
        db.execute(
            select(PerspectiveSnapshot).order_by(
                PerspectiveSnapshot.perspective_id,
                PerspectiveSnapshot.taken_at,
                PerspectiveSnapshot.id,
            )
        )
        .scalars()
        .all()
    )
    events = list(
        db.execute(
            select(PerspectiveEvent).order_by(
                PerspectiveEvent.perspective_id,
                PerspectiveEvent.created_at,
                PerspectiveEvent.id,
            )
        )
        .scalars()
        .all()
    )

    snapshots_by_perspective: Dict[Any, List[PerspectiveSnapshot]] = {}
    for snapshot in snapshots:
        snapshots_by_perspective.setdefault(snapshot.perspective_id, []).append(snapshot)

    events_by_perspective: Dict[Any, List[PerspectiveEvent]] = {}
    for event in events:
        events_by_perspective.setdefault(event.perspective_id, []).append(event)

    now = _utc_now()
    result: List[Dict[str, Any]] = []

    for perspective in perspectives:
        perspective_snapshots = snapshots_by_perspective.get(perspective.id, [])
        perspective_events = events_by_perspective.get(perspective.id, [])

        latest_snapshot = perspective_snapshots[-1] if perspective_snapshots else None
        last_snapshot_at = latest_snapshot.taken_at if latest_snapshot is not None else None
        if last_snapshot_at is not None:
            snapshot_age_hours = round(
                max(0.0, (now - _as_utc(last_snapshot_at)).total_seconds() / 3600.0), 2
            )
        else:
            snapshot_age_hours = None

        unseen_event_count = sum(1 for event in perspective_events if not bool(event.seen))
        tier_change_count = sum(1 for event in perspective_events if _is_tier_change(event))

        result.append(
            {
                "perspective_id": perspective.id,
                "name": perspective.name,
                "last_snapshot_at": last_snapshot_at,
                "snapshot_age_hours": snapshot_age_hours,
                "snapshot_count": len(perspective_snapshots),
                "membership_size": (
                    _membership_size(latest_snapshot.membership) if latest_snapshot is not None else 0
                ),
                "unseen_event_count": unseen_event_count,
                "tier_change_count": tier_change_count,
            }
        )

    return result


def get_perspective_snapshot_ageing(db: Session) -> List[Dict[str, Any]]:
    """Alias for the perspective snapshot ageing computation."""
    return get_snapshot_ageing(db)


def compute_snapshot_ageing(db: Session) -> List[Dict[str, Any]]:
    """Alias for the perspective snapshot ageing computation."""
    return get_snapshot_ageing(db)


def get_snapshot_ageing_report(db: Session) -> Dict[str, Any]:
    """Return the complete snapshot ageing report payload."""
    return {
        "generated_at": _utc_now(),
        "perspectives": get_snapshot_ageing(db),
    }


if __name__ == "__main__":
    print("PASS")