from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


router = APIRouter(prefix="/api")


def _membership_size(membership: Any) -> int:
    if membership is None:
        return 0
    if isinstance(membership, (list, tuple, set)):
        return len(membership)
    if isinstance(membership, dict):
        return len(membership)
    return 0


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


@router.get("/perspectives/snapshot-growth")
def get_snapshot_growth(
    days: int = Query(30, ge=0),
    perspective_id: str | None = Query(None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=days)

    perspectives_query = select(Perspective)
    if perspective_id is not None:
        perspectives_query = perspectives_query.where(Perspective.id == perspective_id)
    perspectives = list(session.scalars(perspectives_query.order_by(Perspective.id)).all())

    perspective_ids = [perspective.id for perspective in perspectives]
    if not perspective_ids:
        return {
            "days": days,
            "perspectives": [],
            "totals": {"perspectives": 0, "snapshots": 0, "events": 0},
        }

    snapshots = list(
        session.scalars(
            select(PerspectiveSnapshot)
            .where(
                PerspectiveSnapshot.perspective_id.in_(perspective_ids),
                PerspectiveSnapshot.taken_at >= cutoff,
            )
            .order_by(
                PerspectiveSnapshot.perspective_id,
                PerspectiveSnapshot.taken_at,
                PerspectiveSnapshot.id,
            )
        ).all()
    )

    events = list(
        session.scalars(
            select(PerspectiveEvent)
            .where(
                PerspectiveEvent.perspective_id.in_(perspective_ids),
                PerspectiveEvent.created_at >= cutoff,
            )
            .order_by(PerspectiveEvent.perspective_id, PerspectiveEvent.created_at, PerspectiveEvent.id)
        ).all()
    )

    snapshots_by_perspective: dict[Any, list[PerspectiveSnapshot]] = {}
    events_by_perspective: dict[Any, list[PerspectiveEvent]] = {}

    for snapshot in snapshots:
        snapshots_by_perspective.setdefault(snapshot.perspective_id, []).append(snapshot)
    for event in events:
        events_by_perspective.setdefault(event.perspective_id, []).append(event)

    rows: list[dict[str, Any]] = []
    total_snapshots = 0
    total_events = 0

    for perspective in perspectives:
        perspective_snapshots = snapshots_by_perspective.get(perspective.id, [])
        perspective_events = events_by_perspective.get(perspective.id, [])

        first_snapshot = perspective_snapshots[0] if perspective_snapshots else None
        last_snapshot = perspective_snapshots[-1] if perspective_snapshots else None

        membership_first = _membership_size(first_snapshot.membership) if first_snapshot else 0
        membership_last = _membership_size(last_snapshot.membership) if last_snapshot else 0

        unseen_events = sum(1 for event in perspective_events if not event.seen)
        tier_transitions = sum(
            1
            for event in perspective_events
            if event.old_tier is not None
            and event.new_tier is not None
            and event.old_tier != event.new_tier
        )

        total_snapshots += len(perspective_snapshots)
        total_events += len(perspective_events)

        rows.append(
            {
                "perspective_id": str(perspective.id),
                "name": perspective.name,
                "snapshots": len(perspective_snapshots),
                "first_taken_at": _timestamp(first_snapshot.taken_at if first_snapshot else None),
                "last_taken_at": _timestamp(last_snapshot.taken_at if last_snapshot else None),
                "membership_first": membership_first,
                "membership_last": membership_last,
                "membership_delta": membership_last - membership_first,
                "events": len(perspective_events),
                "unseen_events": unseen_events,
                "tier_transitions": tier_transitions,
            }
        )

    return {
        "days": days,
        "perspectives": rows,
        "totals": {
            "perspectives": len(rows),
            "snapshots": total_snapshots,
            "events": total_events,
        },
    }


if __name__ == "__main__":
    print("PASS")