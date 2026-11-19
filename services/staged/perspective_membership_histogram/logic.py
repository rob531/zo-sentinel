from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


BUCKETS = ("0", "1-10", "11-50", "51-200", "200+")


def _bucket_for_count(count: int) -> str:
    if count <= 0:
        return "0"
    if count <= 10:
        return "1-10"
    if count <= 50:
        return "11-50"
    if count <= 200:
        return "51-200"
    return "200+"


def _decode_membership(value: Any) -> List[Any]:
    """Decode snapshot membership defensively, treating invalid data as empty."""
    if value is None:
        return []

    decoded: Any = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    if isinstance(decoded, dict):
        members = decoded.get("members")
        return list(members) if isinstance(members, list) else []

    if isinstance(decoded, (list, tuple)):
        return list(decoded)

    return []


def _server_id(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in ("server_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return str(candidate)
    return None


def _members_from_snapshot(value: Any) -> List[str]:
    members: List[str] = []
    for item in _decode_membership(value):
        server_id = _server_id(item)
        if server_id is not None:
            members.append(server_id)
    return members


def _load_perspectives(db: Session) -> List[Tuple[Any, ...]]:
    return db.execute(
        select(
            Perspective.id,
            Perspective.org_id,
            Perspective.name,
            Perspective.description,
            Perspective.facet_filters,
            Perspective.created_by,
            Perspective.created_at,
            Perspective.updated_at,
        ).order_by(Perspective.id)
    ).all()


def _load_snapshots(db: Session) -> List[Tuple[Any, ...]]:
    return db.execute(
        select(
            PerspectiveSnapshot.id,
            PerspectiveSnapshot.perspective_id,
            PerspectiveSnapshot.taken_at,
            PerspectiveSnapshot.membership,
        ).order_by(
            PerspectiveSnapshot.perspective_id,
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
    ).all()


def _latest_snapshots(
    snapshots: Iterable[Tuple[Any, ...]],
) -> Dict[Any, Tuple[Any, List[str]]]:
    latest: Dict[Any, Tuple[Any, List[str]]] = {}
    for snapshot_id, perspective_id, taken_at, membership in snapshots:
        if perspective_id in latest:
            continue
        latest[perspective_id] = (taken_at, _members_from_snapshot(membership))
    return latest


def membership_histogram(db: Session) -> Dict[str, Any]:
    """Return the perspective membership histogram from the real app tables."""
    perspectives = _load_perspectives(db)
    snapshots = _load_snapshots(db)
    latest = _latest_snapshots(snapshots)

    perspective_rows: List[Dict[str, Any]] = []
    bucket_counts: Dict[str, int] = {bucket: 0 for bucket in BUCKETS}
    total_members = 0

    for perspective_id, _org_id, name, _description, _facet_filters, _created_by, _created_at, _updated_at in perspectives:
        snapshot = latest.get(perspective_id)
        members = snapshot[1] if snapshot is not None else []
        member_count = len(members)
        bucket = _bucket_for_count(member_count)

        perspective_rows.append(
            {
                "perspective_id": perspective_id,
                "name": name,
                "latest_snapshot_at": snapshot[0] if snapshot is not None else None,
                "member_count": member_count,
                "bucket": bucket,
            }
        )
        bucket_counts[bucket] += 1
        total_members += member_count

    return {
        "perspectives": perspective_rows,
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in BUCKETS
        ],
        "totals": {
            "perspectives": len(perspective_rows),
            "members": total_members,
        },
    }


def perspective_members(db: Session, perspective_id: Any) -> Optional[Dict[str, Any]]:
    """Return the members in the latest snapshot for one perspective."""
    perspective = db.execute(
        select(
            Perspective.id,
            Perspective.org_id,
            Perspective.name,
            Perspective.description,
            Perspective.facet_filters,
            Perspective.created_by,
            Perspective.created_at,
            Perspective.updated_at,
        ).where(Perspective.id == perspective_id)
    ).first()

    if perspective is None:
        return None

    snapshot = db.execute(
        select(
            PerspectiveSnapshot.id,
            PerspectiveSnapshot.perspective_id,
            PerspectiveSnapshot.taken_at,
            PerspectiveSnapshot.membership,
        )
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
    ).first()

    if snapshot is None:
        return {
            "perspective_id": perspective_id,
            "taken_at": None,
            "members": [],
        }

    _snapshot_id, _snapshot_perspective_id, taken_at, membership = snapshot
    return {
        "perspective_id": perspective_id,
        "taken_at": taken_at,
        "members": _members_from_snapshot(membership),
    }


# Naming aliases keep the logic layer convenient for thin routers.
get_membership_histogram = membership_histogram
get_perspective_members = perspective_members
histogram = membership_histogram
members = perspective_members


if __name__ == "__main__":
    assert _bucket_for_count(0) == "0"
    assert _bucket_for_count(1) == "1-10"
    assert _bucket_for_count(10) == "1-10"
    assert _bucket_for_count(11) == "11-50"
    assert _bucket_for_count(50) == "11-50"
    assert _bucket_for_count(51) == "51-200"
    assert _bucket_for_count(200) == "51-200"
    assert _bucket_for_count(201) == "200+"
    assert _members_from_snapshot(None) == []
    assert _members_from_snapshot("not-json") == []
    assert _members_from_snapshot(json.dumps(["a", "b"])) == ["a", "b"]
    print("PASS")