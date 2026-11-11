from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


class PerspectiveChangesResponse(BaseModel):
    perspective_id: str
    name: Optional[str]
    days: int
    total_events: int
    distinct_servers: int
    by_change_type: Dict[str, int]
    transitions: List["TierTransition"]
    series: List["DailyEventCount"]
    latest_snapshot: "SnapshotSummary"
    unseen_count: int


class TierTransition(BaseModel):
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int


class DailyEventCount(BaseModel):
    date: str
    count: int


class SnapshotSummary(BaseModel):
    taken_at: Optional[str]
    membership_size: int


PerspectiveChangesResponse.model_rebuild()
TierTransition.model_rebuild()
DailyEventCount.model_rebuild()
SnapshotSummary.model_rebuild()


router = APIRouter(tags=["perspective-change-stream"])


def _canonical_server_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def _member_identifier(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("server_id", "serverId", "id"):
            if key in value:
                return _canonical_server_id(value[key])
    return _canonical_server_id(value)


def _membership_size(value: Any) -> int:
    if value is None:
        return 0

    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return 0

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            value = json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0

    try:
        if isinstance(value, list):
            identifiers = {
                identifier
                for identifier in (_member_identifier(item) for item in value)
                if identifier is not None
            }
            return len(identifiers)

        if isinstance(value, dict):
            for key in ("servers", "server_ids", "serverIds", "members", "membership"):
                if key in value:
                    return _membership_size(value[key])

            identifiers = {
                identifier
                for identifier in (_canonical_server_id(key) for key in value.keys())
                if identifier is not None
            }
            return len(identifiers)
    except (TypeError, ValueError, AttributeError):
        return 0

    return 0


def _event_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date()
    return None


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _tier(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def get_perspective_changes(
    db: Session,
    perspective_id: str,
    days: int = 14,
    include_unseen: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    cutoff = current_time - timedelta(days=days)

    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()

    events = list(
        db.execute(
            select(PerspectiveEvent)
            .where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.created_at >= cutoff,
            )
            .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
        ).scalars()
    )

    unseen_count = sum(1 for event in events if not bool(event.seen))
    selected_events = [
        event for event in events if include_unseen or bool(event.seen)
    ]

    by_change_type: Dict[str, int] = {}
    transition_counts: Dict[Tuple[Optional[str], Optional[str]], int] = {}
    distinct_servers = set()
    daily_counts: Dict[date, int] = {}

    for event in selected_events:
        change_type = str(event.change_type)
        by_change_type[change_type] = by_change_type.get(change_type, 0) + 1

        transition = (_tier(event.old_tier), _tier(event.new_tier))
        transition_counts[transition] = transition_counts.get(transition, 0) + 1

        server_id = _canonical_server_id(event.server_id)
        if server_id is not None:
            distinct_servers.add(server_id)

        event_date = _event_date(event.created_at)
        if event_date is not None:
            daily_counts[event_date] = daily_counts.get(event_date, 0) + 1

    transitions = [
        {"old_tier": old_tier, "new_tier": new_tier, "count": count}
        for (old_tier, new_tier), count in sorted(
            transition_counts.items(),
            key=lambda item: (item[0][0] is not None, item[0][0] or "", item[0][1] is not None, item[0][1] or ""),
        )
    ]

    start_date = cutoff.date()
    current_date = current_time.date()
    series = []
    cursor = start_date
    while cursor <= current_date:
        series.append({"date": cursor.isoformat(), "count": daily_counts.get(cursor, 0)})
        cursor += timedelta(days=1)

    snapshot = db.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc(), PerspectiveSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    latest_snapshot = {
        "taken_at": _isoformat(snapshot.taken_at) if snapshot is not None else None,
        "membership_size": _membership_size(snapshot.membership) if snapshot is not None else 0,
    }

    return {
        "perspective_id": str(perspective_id),
        "name": str(perspective.name) if perspective is not None else None,
        "days": days,
        "total_events": len(selected_events),
        "distinct_servers": len(distinct_servers),
        "by_change_type": by_change_type,
        "transitions": transitions,
        "series": series,
        "latest_snapshot": latest_snapshot,
        "unseen_count": unseen_count,
    }


def compute_perspective_changes(
    db: Session,
    perspective_id: str,
    days: int = 14,
    include_unseen: bool = False,
) -> Dict[str, Any]:
    return get_perspective_changes(
        db,
        perspective_id=perspective_id,
        days=days,
        include_unseen=include_unseen,
    )


get_change_stream = compute_perspective_changes


@router.get(
    "/perspectives/{perspective_id}/changes",
    response_model=PerspectiveChangesResponse,
)
@router.get(
    "/api/perspectives/{perspective_id}/changes",
    response_model=PerspectiveChangesResponse,
    include_in_schema=False,
)
def perspective_changes(
    perspective_id: str,
    days: int = Query(default=14, ge=0),
    include_unseen: bool = Query(default=False),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_perspective_changes(
        db,
        perspective_id=perspective_id,
        days=days,
        include_unseen=include_unseen,
    )


if __name__ == "__main__":
    assert _membership_size(None) == 0
    assert _membership_size([]) == 0
    assert _membership_size(["server-a", "server-b"]) == 2
    assert _membership_size({"servers": ["server-a", "server-b"]}) == 2
    assert _membership_size('{"servers":["server-a"]}') == 1
    assert _membership_size("not-json") == 0
    assert _canonical_server_id(None) is None
    print("PASS")