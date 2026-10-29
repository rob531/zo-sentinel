from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

app = FastAPI()

_JOIN_TYPES = {"join", "added", "member_added"}
_LEAVE_TYPES = {"leave", "removed", "member_removed"}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            return _as_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def _validate_window(days: int, now: datetime | None) -> tuple[int, datetime]:
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ValueError("days must be a positive integer")
    if now is None:
        now = datetime.now(timezone.utc)
    elif not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    return days, _as_utc(now)


def _change_type(row: dict) -> str:
    value = row.get("change_type")
    return value.strip().lower() if isinstance(value, str) else ""


def _is_tier_change(row: dict) -> bool:
    old_tier = row.get("old_tier")
    new_tier = row.get("new_tier")
    return old_tier is not None and new_tier is not None and old_tier != new_tier


def _membership_size(snapshot: dict | None) -> int:
    if not snapshot:
        return 0
    membership = snapshot.get("membership")
    if not isinstance(membership, list):
        return 0
    return len({member for member in membership if member is not None})


def _latest_snapshots(snapshots: list[dict]) -> dict[Any, dict]:
    latest: dict[Any, tuple[datetime, int, dict]] = {}

    for index, snapshot in enumerate(snapshots):
        perspective_id = snapshot.get("perspective_id")
        taken_at = _parse_datetime(snapshot.get("taken_at"))
        ordering_time = taken_at or datetime.min.replace(tzinfo=timezone.utc)
        previous = latest.get(perspective_id)
        if previous is None or (ordering_time, index) >= (previous[0], previous[1]):
            latest[perspective_id] = (ordering_time, index, snapshot)

    return {perspective_id: item[2] for perspective_id, item in latest.items()}


def summarise(
    rows: list[dict],
    snapshots: list[dict],
    now: datetime | None = None,
) -> dict:
    days, current_time = _validate_window(days=30, now=now)
    cutoff = current_time - timedelta(days=days)
    latest_snapshots = _latest_snapshots(snapshots)

    grouped_events: dict[Any, list[dict]] = {}
    grouped_names: dict[Any, str] = {}
    total_joins = 0
    total_leaves = 0
    total_tier_changes = 0
    joined_servers: set[Any] = set()
    left_servers: set[Any] = set()
    seen_servers: set[Any] = set()

    for row in rows:
        created_at = _parse_datetime(row.get("created_at"))
        if created_at is None or created_at < cutoff:
            continue

        perspective_id = row.get("perspective_id")
        grouped_events.setdefault(perspective_id, []).append(row)

        name = row.get("name")
        if name and not grouped_names.get(perspective_id):
            grouped_names[perspective_id] = str(name)

        server_id = row.get("server_id")
        if server_id is not None:
            seen_servers.add(server_id)

        change_type = _change_type(row)
        if change_type in _JOIN_TYPES:
            total_joins += 1
            if server_id is not None:
                joined_servers.add(server_id)
        elif change_type in _LEAVE_TYPES:
            total_leaves += 1
            if server_id is not None:
                left_servers.add(server_id)

        if _is_tier_change(row):
            total_tier_changes += 1

    for perspective_id, snapshot in latest_snapshots.items():
        name = snapshot.get("name")
        if name and not grouped_names.get(perspective_id):
            grouped_names[perspective_id] = str(name)

    perspective_ids = set(grouped_events) | set(latest_snapshots)
    perspectives = []

    for perspective_id in sorted(perspective_ids, key=lambda value: str(value)):
        events = grouped_events.get(perspective_id, [])
        joins = 0
        leaves = 0
        tier_changes = 0
        server_counts: Counter[Any] = Counter()

        for event in events:
            change_type = _change_type(event)
            if change_type in _JOIN_TYPES:
                joins += 1
            elif change_type in _LEAVE_TYPES:
                leaves += 1
            if _is_tier_change(event):
                tier_changes += 1
            server_id = event.get("server_id")
            if server_id is not None:
                server_counts[server_id] += 1

        top_servers = [
            {"server_id": server_id, "count": count}
            for server_id, count in sorted(
                server_counts.items(),
                key=lambda item: (-item[1], str(item[0])),
            )
        ]

        perspectives.append(
            {
                "perspective_id": perspective_id,
                "name": grouped_names.get(perspective_id, ""),
                "events": events,
                "joins": joins,
                "leaves": leaves,
                "tier_changes": tier_changes,
                "top_servers": top_servers,
                "membership_size": _membership_size(
                    latest_snapshots.get(perspective_id)
                ),
            }
        )

    return {
        "window_days": days,
        "servers_seen": len(seen_servers),
        "servers_joined": len(joined_servers),
        "servers_left": len(left_servers),
        "tier_changes": total_tier_changes,
        "joins": total_joins,
        "leaves": total_leaves,
        "perspectives": perspectives,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _report_from_session(session: Any, days: int, now: datetime | None) -> dict:
    event_models = session.query(PerspectiveEvent).all()
    snapshot_models = session.query(PerspectiveSnapshot).all()
    perspective_models = session.query(Perspective).all()

    names = {
        perspective.id: perspective.name
        for perspective in perspective_models
        if perspective.name is not None
    }

    rows = [
        {
            "perspective_id": event.perspective_id,
            "server_id": event.server_id,
            "change_type": event.change_type,
            "old_tier": event.old_tier,
            "new_tier": event.new_tier,
            "seen": event.seen,
            "created_at": event.created_at,
            "name": names.get(event.perspective_id, ""),
        }
        for event in event_models
    ]

    snapshots = [
        {
            "perspective_id": snapshot.perspective_id,
            "taken_at": snapshot.taken_at,
            "membership": snapshot.membership,
            "name": names.get(snapshot.perspective_id, ""),
        }
        for snapshot in snapshot_models
    ]

    return summarise(rows, snapshots, now)


def build_report(days: int = 30, now: datetime | None = None) -> dict:
    session_context = get_session()
    session = next(session_context)
    try:
        return _report_from_session(session, days, now)
    finally:
        close = getattr(session_context, "close", None)
        if close is not None:
            close()


@app.get("/reports/perspective-membership-churn")
def perspective_membership_churn_report(
    days: int = 30,
    session: Any = Depends(get_session),
) -> dict:
    return _report_from_session(session, days, None)


if __name__ == "__main__":
    now = datetime(2025, 1, 31, tzinfo=timezone.utc)
    synthetic_rows = [
        {
            "perspective_id": "perspective-1",
            "server_id": "server-a",
            "change_type": "tier_changed",
            "old_tier": "core",
            "new_tier": "preferred",
            "seen": True,
            "created_at": now - timedelta(days=2),
        },
        {
            "perspective_id": "perspective-1",
            "server_id": "server-b",
            "change_type": "join",
            "old_tier": None,
            "new_tier": "core",
            "seen": True,
            "created_at": now - timedelta(days=3),
        },
        {
            "perspective_id": "perspective-1",
            "server_id": "server-c",
            "change_type": "leave",
            "old_tier": "core",
            "new_tier": None,
            "seen": True,
            "created_at": now - timedelta(days=4),
        },
        {
            "perspective_id": "perspective-1",
            "server_id": "server-old",
            "change_type": "join",
            "old_tier": None,
            "new_tier": "core",
            "seen": True,
            "created_at": now - timedelta(days=31),
        },
    ]
    synthetic_snapshots = [
        {
            "perspective_id": "perspective-1",
            "taken_at": now - timedelta(days=1),
            "membership": ["server-a", "server-b"],
        },
        {
            "perspective_id": "perspective-1",
            "taken_at": now - timedelta(days=10),
            "membership": ["server-a", "server-b", "server-c"],
        },
    ]

    report = summarise(synthetic_rows, synthetic_snapshots, now)

    assert report["window_days"] == 30
    assert report["tier_changes"] == 1
    assert report["joins"] == 1
    assert report["leaves"] == 1
    assert report["servers_joined"] == 1
    assert report["servers_left"] == 1
    assert all(
        event.get("server_id") != "server-old"
        for event in report["perspectives"][0]["events"]
    )
    datetime.fromisoformat(report["generated_at"])
    print("PASS")