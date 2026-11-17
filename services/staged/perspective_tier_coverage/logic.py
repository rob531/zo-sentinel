"""Logic for perspective tier coverage."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent, PerspectiveSnapshot


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _membership_server_ids(membership: Any) -> list[str]:
    """Parse a membership blob without allowing malformed data to escape."""
    if membership is None:
        return []

    value = membership
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    if not isinstance(value, list):
        return []

    server_ids: list[str] = []
    for member in value:
        if isinstance(member, Mapping):
            member = member.get("server_id")
        if member is None:
            continue
        server_id = str(member)
        if server_id and server_id not in server_ids:
            server_ids.append(server_id)
    return server_ids


def _sortable_timestamp(value: Any) -> tuple[int, Any]:
    if isinstance(value, datetime):
        return (2, value.timestamp())
    if isinstance(value, date):
        return (1, value.toordinal())
    if isinstance(value, (int, float)):
        return (1, value)
    if isinstance(value, str):
        try:
            return (1, datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return (0, value)
    return (0, "")


def _is_seen(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "n", "none"}
    return bool(value)


def _registry_lookup(registry_tiers: Any) -> Mapping[Any, Any]:
    if isinstance(registry_tiers, Mapping):
        return registry_tiers
    if isinstance(registry_tiers, Iterable):
        result: Dict[Any, Any] = {}
        for row in registry_tiers:
            if isinstance(row, Mapping):
                server_id = row.get("server_id")
                tier = row.get("risk_tier")
            else:
                try:
                    server_id, tier = row
                except (TypeError, ValueError):
                    continue
            result[server_id] = tier
        return result
    return {}


def summarize_perspective_coverage(
    perspectives: Iterable[Any],
    snapshots: Iterable[Any],
    events: Iterable[Any],
    registry_tiers: Any,
) -> Dict[str, Any]:
    """Summarize current tier coverage and unseen tier-change events."""
    perspective_rows = list(perspectives)
    snapshot_rows = list(snapshots)
    event_rows = list(events)
    registry = _registry_lookup(registry_tiers)

    latest_snapshots: Dict[Any, Any] = {}
    for snapshot in snapshot_rows:
        perspective_id = _value(snapshot, "perspective_id")
        current = latest_snapshots.get(perspective_id)
        if current is None or _sortable_timestamp(_value(snapshot, "taken_at")) >= _sortable_timestamp(
            _value(current, "taken_at")
        ):
            latest_snapshots[perspective_id] = snapshot

    perspective_results: list[Dict[str, Any]] = []
    total_members = 0

    for perspective in perspective_rows:
        perspective_id = _value(perspective, "id")
        snapshot = latest_snapshots.get(perspective_id)
        member_ids = _membership_server_ids(_value(snapshot, "membership")) if snapshot else []
        tiers: Dict[str, int] = {}

        for server_id in member_ids:
            tier = registry.get(server_id)
            tier_name = str(tier) if tier not in (None, "") else "unknown"
            tiers[tier_name] = tiers.get(tier_name, 0) + 1

        member_count = len(member_ids)
        total_members += member_count
        perspective_results.append(
            {
                "perspective_id": perspective_id,
                "name": _value(perspective, "name"),
                "member_count": member_count,
                "tiers": tiers,
                "last_snapshot_at": _value(snapshot, "taken_at") if snapshot else None,
            }
        )

    pending: list[Dict[str, Any]] = []
    tier_up_events = 0
    tier_down_events = 0

    for event in event_rows:
        if _is_seen(_value(event, "seen")):
            continue

        change_type = _value(event, "change_type")
        change_name = str(change_type).lower() if change_type is not None else ""
        if "up" in change_name:
            tier_up_events += 1
        elif "down" in change_name:
            tier_down_events += 1

        pending.append(
            {
                "perspective_id": _value(event, "perspective_id"),
                "server_id": _value(event, "server_id"),
                "change_type": change_type,
                "old_tier": _value(event, "old_tier"),
                "new_tier": _value(event, "new_tier"),
                "created_at": _value(event, "created_at"),
            }
        )

    return {
        "perspectives": perspective_results,
        "totals": {
            "perspectives": len(perspective_rows),
            "members": total_members,
            "unseen_events": len(pending),
            "tier_up_events": tier_up_events,
            "tier_down_events": tier_down_events,
        },
        "events": {"pending": pending},
    }


def get_perspective_coverage(
    db: Session,
    perspective_id: int | None = None,
) -> Dict[str, Any]:
    """Read the real application tables and summarize their coverage."""
    perspective_query = select(Perspective).order_by(Perspective.id)
    if perspective_id is not None:
        perspective_query = perspective_query.where(Perspective.id == perspective_id)

    perspectives = list(db.execute(perspective_query).scalars().all())
    perspective_ids = {perspective.id for perspective in perspectives}

    snapshots = list(
        db.execute(
            select(PerspectiveSnapshot)
            .where(PerspectiveSnapshot.perspective_id.in_(perspective_ids))
            .order_by(PerspectiveSnapshot.taken_at)
        )
        .scalars()
        .all()
    )
    events = list(
        db.execute(
            select(PerspectiveEvent)
            .where(PerspectiveEvent.perspective_id.in_(perspective_ids))
            .order_by(PerspectiveEvent.created_at)
        )
        .scalars()
        .all()
    )
    registry_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
    ).all()
    registry_tiers = {server_id: tier for server_id, tier in registry_rows}

    return summarize_perspective_coverage(
        perspectives,
        snapshots,
        events,
        registry_tiers,
    )


def fetch_perspective_coverage(
    db: Session,
    perspective_id: int | None = None,
) -> Dict[str, Any]:
    """Compatibility name for the read-only coverage query."""
    return get_perspective_coverage(db, perspective_id)


if __name__ == "__main__":
    _coverage = summarize_perspective_coverage(
        [
            {"id": 1, "name": "Watchlist"},
            {"id": 2, "name": "Portfolio"},
        ],
        [
            {"perspective_id": 1, "taken_at": "2025-01-01T00:00:00", "membership": '["a", "b", "c"]'},
            {"perspective_id": 2, "taken_at": "2025-01-01T00:00:00", "membership": '["a", "b", "c"]'},
        ],
        [
            {
                "perspective_id": 1,
                "server_id": "a",
                "change_type": "tier_up",
                "old_tier": "medium",
                "new_tier": "high",
                "seen": False,
                "created_at": "2025-01-01T00:00:00",
            },
            {
                "perspective_id": 2,
                "server_id": "b",
                "change_type": "tier_down",
                "old_tier": "high",
                "new_tier": "low",
                "seen": False,
                "created_at": "2025-01-01T00:00:00",
            },
        ],
        {"a": "high", "b": "medium", "c": "low"},
    )
    assert _coverage["totals"]["perspectives"] == 2
    assert _coverage["perspectives"][0]["member_count"] == 3
    assert _coverage["totals"]["unseen_events"] == 2
    assert len(_coverage["events"]["pending"]) == 2
    print("PASS")