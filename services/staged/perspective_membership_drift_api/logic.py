from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent, PerspectiveSnapshot


def _decode_membership(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _member_id(member: Any) -> str | None:
    if isinstance(member, (str, int, float)):
        return str(member)
    if not isinstance(member, dict):
        return None

    for key in ("server_id", "serverId", "id"):
        item = member.get(key)
        if item is not None:
            return str(item)
    return None


def _member_tier(member: Any) -> Any:
    if not isinstance(member, dict):
        return None
    for key in ("snapshot_tier", "risk_tier", "tier", "old_tier"):
        if key in member and member[key] is not None:
            return member[key]
    return None


def _member_name(member: Any) -> str | None:
    if not isinstance(member, dict):
        return None
    for key in ("name", "server_name"):
        if member.get(key) is not None:
            return str(member[key])
    return None


def _extract_members(raw_membership: Any) -> list[dict[str, Any]]:
    membership = _decode_membership(raw_membership)
    members: list[dict[str, Any]] = []

    if isinstance(membership, list):
        for item in membership:
            server_id = _member_id(item)
            if server_id is not None:
                members.append(
                    {
                        "server_id": server_id,
                        "tier": _member_tier(item),
                        "name": _member_name(item),
                    }
                )
        return members

    if not isinstance(membership, dict):
        return members

    for key in ("servers", "members", "membership", "server_ids"):
        if key in membership:
            members.extend(_extract_members(membership[key]))

    for key in ("server_tiers", "tiers", "risks", "risk_tiers"):
        values = membership.get(key)
        if isinstance(values, dict):
            for server_id, tier in values.items():
                members.append(
                    {
                        "server_id": str(server_id),
                        "tier": tier,
                        "name": None,
                    }
                )

    if _member_id(membership) is not None:
        members.append(
            {
                "server_id": _member_id(membership),
                "tier": _member_tier(membership),
                "name": _member_name(membership),
            }
        )
    elif not members:
        metadata_keys = {
            "count",
            "member_count",
            "taken_at",
            "perspective_id",
            "generated_at",
            "version",
        }
        for server_id, value in membership.items():
            if server_id in metadata_keys:
                continue
            if isinstance(value, dict):
                tier = _member_tier(value)
                name = _member_name(value)
            else:
                tier = value
                name = None
            members.append(
                {
                    "server_id": str(server_id),
                    "tier": tier,
                    "name": name,
                }
            )

    unique: dict[str, dict[str, Any]] = {}
    for member in members:
        existing = unique.get(member["server_id"])
        if existing is None or (
            existing.get("tier") is None and member.get("tier") is not None
        ):
            unique[member["server_id"]] = member
    return list(unique.values())


def _numeric_tier(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


_TIER_RANKS = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "moderate": 2,
    "low": 1,
    "safe": 0,
    "unknown": 0,
}


def _tier_delta(snapshot_tier: Any, current_tier: Any) -> int:
    snapshot_number = _numeric_tier(snapshot_tier)
    current_number = _numeric_tier(current_tier)
    if snapshot_number is not None and current_number is not None:
        return int(current_number - snapshot_number)

    snapshot_rank = _TIER_RANKS.get(str(snapshot_tier).strip().lower())
    current_rank = _TIER_RANKS.get(str(current_tier).strip().lower())
    if snapshot_rank is not None and current_rank is not None:
        return current_rank - snapshot_rank
    return 0


def _tiers_equal(left: Any, right: Any) -> bool:
    if left == right:
        return True
    left_number = _numeric_tier(left)
    right_number = _numeric_tier(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left).strip().lower() == str(right).strip().lower()


def get_membership_drift(db: Session) -> dict[str, Any]:
    perspectives = list(db.execute(select(Perspective).order_by(Perspective.id)).scalars())

    perspective_ids = [perspective.id for perspective in perspectives]
    snapshots: dict[int, list[PerspectiveSnapshot]] = {}
    if perspective_ids:
        snapshot_rows = db.execute(
            select(PerspectiveSnapshot)
            .where(PerspectiveSnapshot.perspective_id.in_(perspective_ids))
            .order_by(
                PerspectiveSnapshot.perspective_id,
                PerspectiveSnapshot.taken_at.desc(),
                PerspectiveSnapshot.id.desc(),
            )
        ).scalars()
        for snapshot in snapshot_rows:
            snapshots.setdefault(snapshot.perspective_id, []).append(snapshot)

    events: list[PerspectiveEvent] = []
    if perspective_ids:
        events = list(
            db.execute(
                select(PerspectiveEvent)
                .where(PerspectiveEvent.perspective_id.in_(perspective_ids))
                .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
            ).scalars()
        )

    servers: dict[str, McpServerRegistry] = {}
    if events:
        server_ids = {event.server_id for event in events if event.server_id is not None}
        if server_ids:
            server_rows = db.execute(
                select(McpServerRegistry).where(McpServerRegistry.server_id.in_(server_ids))
            ).scalars()
            servers = {server.server_id: server for server in server_rows}

    unseen_by_perspective: dict[int, list[PerspectiveEvent]] = {}
    for event in events:
        if event.seen is False:
            unseen_by_perspective.setdefault(event.perspective_id, []).append(event)

    result_perspectives: list[dict[str, Any]] = []
    for perspective in perspectives:
        perspective_snapshots = snapshots.get(perspective.id, [])
        latest_snapshot = (
            perspective_snapshots[0] if perspective_snapshots else None
        )
        members = (
            _extract_members(latest_snapshot.membership) if latest_snapshot else []
        )
        unseen_events = unseen_by_perspective.get(perspective.id, [])
        unseen_by_server: dict[str, list[PerspectiveEvent]] = {}
        for event in unseen_events:
            if event.server_id is not None:
                unseen_by_server.setdefault(event.server_id, []).append(event)

        drifted: list[dict[str, Any]] = []
        for member in members:
            server_id = member["server_id"]
            server = servers.get(server_id)
            if server is None:
                continue

            snapshot_tier = member.get("tier")
            if snapshot_tier is None:
                matching_events = unseen_by_server.get(server_id, [])
                for event in matching_events:
                    if event.old_tier is not None:
                        snapshot_tier = event.old_tier
                        break

            current_tier = server.risk_tier
            if snapshot_tier is None or current_tier is None:
                continue
            if _tiers_equal(snapshot_tier, current_tier):
                continue
            if server_id not in unseen_by_server:
                continue

            drifted.append(
                {
                    "server_id": server_id,
                    "name": server.name or member.get("name") or server_id,
                    "snapshot_tier": snapshot_tier,
                    "current_tier": current_tier,
                    "delta": _tier_delta(snapshot_tier, current_tier),
                }
            )

        drifted.sort(key=lambda item: item["server_id"])
        result_perspectives.append(
            {
                "perspective_id": perspective.id,
                "name": perspective.name,
                "snapshot_taken_at": (
                    latest_snapshot.taken_at.isoformat()
                    if latest_snapshot is not None
                    and isinstance(latest_snapshot.taken_at, datetime)
                    else latest_snapshot.taken_at
                    if latest_snapshot is not None
                    else None
                ),
                "member_count": len(members),
                "drifted": drifted,
                "unseen_event_count": len(unseen_events),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "perspectives": result_perspectives,
    }


build_membership_drift = get_membership_drift
compute_membership_drift = get_membership_drift
generate_membership_drift = get_membership_drift
generate_membership_drift_report = get_membership_drift
get_membership_drift_report = get_membership_drift
build_membership_drift_report = get_membership_drift


if __name__ == "__main__":
    parsed = _extract_members(
        {
            "servers": [
                {"server_id": "server-1", "risk_tier": 1, "name": "Example"},
            ]
        }
    )
    assert parsed == [
        {"server_id": "server-1", "tier": 1, "name": "Example"}
    ]
    print("PASS")