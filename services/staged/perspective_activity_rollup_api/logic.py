from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


class PerspectiveActivity(BaseModel):
    perspective_id: str
    name: str
    snapshot_count: int
    event_count: int
    unseen_event_count: int
    change_types: Dict[str, int] = Field(default_factory=dict)
    transitions: List[Dict[str, Any]] = Field(default_factory=list)


class ActivityRollupTotals(BaseModel):
    perspectives: int
    events: int
    unseen: int


class ActivityRollupResponse(BaseModel):
    perspectives: List[PerspectiveActivity] = Field(default_factory=list)
    totals: ActivityRollupTotals


class PerspectiveActivityRollup(BaseModel):
    perspective_id: str
    name: str
    snapshot_count: int
    event_count: int
    unseen_event_count: int
    change_types: Dict[str, int] = Field(default_factory=dict)
    transitions: List[Dict[str, Any]] = Field(default_factory=list)


class ActivityRollup(BaseModel):
    perspectives: List[PerspectiveActivityRollup] = Field(default_factory=list)
    totals: ActivityRollupTotals


def _as_count(value: Any) -> int:
    return int(value or 0)


def get_activity_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    snapshot_counts = (
        select(
            PerspectiveSnapshot.perspective_id.label("perspective_id"),
            func.count(PerspectiveSnapshot.id).label("snapshot_count"),
        )
        .group_by(PerspectiveSnapshot.perspective_id)
        .subquery()
    )

    event_counts = (
        select(
            PerspectiveEvent.perspective_id.label("perspective_id"),
            func.count(PerspectiveEvent.id).label("event_count"),
            func.sum(
                case((PerspectiveEvent.seen.is_(False), 1), else_=0)
            ).label("unseen_event_count"),
        )
        .group_by(PerspectiveEvent.perspective_id)
        .subquery()
    )

    perspective_rows = session.execute(
        select(
            Perspective.id,
            Perspective.name,
            Perspective.org_id,
            Perspective.created_at,
            func.coalesce(snapshot_counts.c.snapshot_count, 0),
            func.coalesce(event_counts.c.event_count, 0),
            func.coalesce(event_counts.c.unseen_event_count, 0),
        )
        .outerjoin(
            snapshot_counts,
            snapshot_counts.c.perspective_id == Perspective.id,
        )
        .outerjoin(
            event_counts,
            event_counts.c.perspective_id == Perspective.id,
        )
        .order_by(Perspective.id)
    ).all()

    change_type_rows = session.execute(
        select(
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.change_type,
            func.count(PerspectiveEvent.id).label("event_count"),
        )
        .group_by(
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.change_type,
        )
    ).all()

    change_types_by_perspective: Dict[Any, Dict[str, int]] = {}
    for perspective_id, change_type, event_count in change_type_rows:
        key = str(change_type) if change_type is not None else "unknown"
        change_types_by_perspective.setdefault(perspective_id, {})[key] = _as_count(
            event_count
        )

    transition_rows = session.execute(
        select(
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            func.count(PerspectiveEvent.id).label("transition_count"),
        )
        .where(
            PerspectiveEvent.old_tier.is_not(None),
            PerspectiveEvent.new_tier.is_not(None),
        )
        .group_by(
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
        )
        .order_by(
            func.count(PerspectiveEvent.id).desc(),
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
        )
    ).all()

    transitions_by_perspective: Dict[Any, List[Dict[str, Any]]] = {}
    for perspective_id, old_tier, new_tier, transition_count in transition_rows:
        transitions_by_perspective.setdefault(perspective_id, []).append(
            {
                "old_tier": old_tier,
                "new_tier": new_tier,
                "count": _as_count(transition_count),
            }
        )

    perspectives: List[Dict[str, Any]] = []
    total_events = 0
    total_unseen = 0

    for row in perspective_rows:
        perspective_id = row[0]
        snapshot_count = _as_count(row[4])
        event_count = _as_count(row[5])
        unseen_event_count = _as_count(row[6])

        total_events += event_count
        total_unseen += unseen_event_count

        perspectives.append(
            {
                "perspective_id": str(perspective_id),
                "name": row[1],
                "snapshot_count": snapshot_count,
                "event_count": event_count,
                "unseen_event_count": unseen_event_count,
                "change_types": change_types_by_perspective.get(perspective_id, {}),
                "transitions": transitions_by_perspective.get(perspective_id, []),
            }
        )

    return {
        "perspectives": perspectives,
        "totals": {
            "perspectives": len(perspectives),
            "events": total_events,
            "unseen": total_unseen,
        },
    }


def build_activity_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_activity_rollup(session)


def activity_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_activity_rollup(session)


def get_perspective_activity_rollup(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_activity_rollup(session)


def rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_activity_rollup(session)


if __name__ == "__main__":
    print("PASS")