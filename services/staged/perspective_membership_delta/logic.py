"""logic.py -- perspective membership delta computation over the real app data layer."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PerspectiveEvent, PerspectiveSnapshot


Membership = Dict[str, Any]


def _parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _tier_value(value: Any) -> Any:
    value = _parse_json(value)
    if isinstance(value, dict):
        for key in ("tier", "risk_tier", "level"):
            if key in value:
                return value[key]
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _normalise_membership(value: Any) -> Membership:
    """Convert the supported JSON membership representations to server -> tier."""
    value = _parse_json(value)
    if not value:
        return {}

    if isinstance(value, dict):
        for key in ("membership", "members", "servers"):
            if key in value:
                nested = value[key]
                if key == "servers" and isinstance(nested, list) and isinstance(value.get("tiers"), dict):
                    return {
                        str(server): _tier_value(value["tiers"].get(server))
                        for server in nested
                    }
                return _normalise_membership(nested)

        return {str(server): _tier_value(tier) for server, tier in value.items()}

    if isinstance(value, (list, tuple, set)):
        result: Membership = {}
        for item in value:
            item = _parse_json(item)
            if isinstance(item, dict):
                server = item.get("server_id", item.get("id", item.get("server")))
                if server is not None:
                    result[str(server)] = _tier_value(item.get("tier", item.get("new_tier")))
            elif item is not None:
                result[str(item)] = None
        return result

    return {}


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def _event_kind(change_type: Any, old_tier: Any, new_tier: Any) -> Optional[str]:
    kind = str(change_type or "").strip().lower().replace("-", "_")
    if kind in {"added", "add", "join", "joined", "membership_added", "server_added"}:
        return "added"
    if kind in {"removed", "remove", "leave", "left", "membership_removed", "server_removed"}:
        return "removed"
    if kind in {"changed", "change", "tier_changed", "tier_change", "updated", "modified"}:
        return "changed"
    if old_tier != new_tier:
        return "changed"
    return None


def _window(days: int) -> Tuple[date, date]:
    safe_days = max(1, min(int(days), 366))
    end = date.today()
    return end - timedelta(days=safe_days - 1), end


def get_membership_delta(db: Session, perspective_id: int, days: int = 14) -> Dict[str, Any]:
    """Compute daily membership additions, removals and tier changes.

    Snapshot membership is the authoritative membership state.  Events are
    used to recover tier transition details and to cover tier changes that
    are present in the event stream but not represented by tier metadata in
    a snapshot.
    """
    start, end = _window(days)
    window_dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]

    snapshots = list(
        db.execute(
            select(PerspectiveSnapshot)
            .where(PerspectiveSnapshot.perspective_id == perspective_id)
            .order_by(PerspectiveSnapshot.taken_at, PerspectiveSnapshot.id)
        ).scalars()
    )
    events = list(
        db.execute(
            select(PerspectiveEvent)
            .where(PerspectiveEvent.perspective_id == perspective_id)
            .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
        ).scalars()
    )

    daily: Dict[date, Dict[str, int]] = {
        current: {"added": 0, "removed": 0, "changed": 0} for current in window_dates
    }
    changed_by_day: Dict[date, Set[str]] = {current: set() for current in window_dates}
    changes_by_server: Dict[str, Dict[str, Any]] = {}

    previous: Optional[Membership] = None
    for snapshot in snapshots:
        current = _normalise_membership(snapshot.membership)
        snapshot_date = _as_date(snapshot.taken_at)
        if snapshot_date is not None and start <= snapshot_date <= end:
            if previous is not None:
                added = set(current) - set(previous)
                removed = set(previous) - set(current)
                changed = {
                    server
                    for server in set(current) & set(previous)
                    if previous[server] != current[server]
                }

                daily[snapshot_date]["added"] += len(added)
                daily[snapshot_date]["removed"] += len(removed)
                daily[snapshot_date]["changed"] += len(changed)
                changed_by_day[snapshot_date].update(changed)

                for server in changed:
                    changes_by_server[server] = {
                        "server_id": server,
                        "old_tier": previous[server],
                        "new_tier": current[server],
                    }
        previous = current

    # Events provide the old/new tier values for snapshot changes.  They
    # also account for a tier change when membership snapshots contain only
    # server IDs and therefore cannot express tier information.
    for event in events:
        event_date = _as_date(event.created_at)
        if event_date is None or not (start <= event_date <= end):
            continue

        kind = _event_kind(event.change_type, event.old_tier, event.new_tier)
        server = str(event.server_id)
        if kind == "changed":
            if server not in changed_by_day[event_date]:
                daily[event_date]["changed"] += 1
                changed_by_day[event_date].add(server)
            changes_by_server[server] = {
                "server_id": server,
                "old_tier": event.old_tier,
                "new_tier": event.new_tier,
            }
        elif kind in {"added", "removed"}:
            # Snapshot diffs are preferred, but an event can legitimately be
            # the only available record for a membership transition.
            if not snapshots:
                daily[event_date][kind] += 1

    series = [
        {
            "date": current.isoformat(),
            "added": daily[current]["added"],
            "removed": daily[current]["removed"],
            "changed": daily[current]["changed"],
        }
        for current in window_dates
    ]

    totals = {
        "servers_added": sum(item["added"] for item in series),
        "servers_removed": sum(item["removed"] for item in series),
        "tier_changes": sum(item["changed"] for item in series),
    }

    return {
        "perspective_id": perspective_id,
        "days": len(window_dates),
        "series": series,
        "totals": totals,
        "changes": list(changes_by_server.values()),
    }


# Names used by thin routers and by callers that treat this module as a
# computation helper rather than an endpoint implementation.
compute_membership_delta = get_membership_delta
membership_delta = get_membership_delta
get_perspective_membership_delta = get_membership_delta


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

    that_app = FastAPI()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/perspectives/{perspective_id}/membership-delta")
    def membership_delta_endpoint(
        perspective_id: int,
        days: int = Query(default=14),
    ):
        session = SessionLocal()
        try:
            return get_membership_delta(session, perspective_id, days)
        finally:
            session.close()

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add(
            Perspective(
                id=1,
                name="contract perspective",
                created_by=1,
                facet_filters={},
                description="contract seed",
            )
        )
        session.add(
            PerspectiveSnapshot(
                id=1,
                perspective_id=1,
                taken_at=now - timedelta(days=1),
                membership={"alpha": "low", "old": "high"},
            )
        )
        session.add(
            PerspectiveSnapshot(
                id=2,
                perspective_id=1,
                taken_at=now,
                membership={"alpha": "high", "new": "high"},
            )
        )
        session.add(
            PerspectiveEvent(
                id=1,
                perspective_id=1,
                server_id="alpha",
                change_type="tier_changed",
                old_tier="low",
                new_tier="high",
                seen=False,
                created_at=now,
            )
        )
        session.add(
            PerspectiveEvent(
                id=2,
                perspective_id=1,
                server_id="old",
                change_type="removed",
                old_tier="high",
                new_tier=None,
                seen=False,
                created_at=now,
            )
        )
        session.commit()

    response = TestClient(that_app).get(
        "/api/perspectives/1/membership-delta",
        params={"days": 14},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["servers_added"] == 1
    assert payload["totals"]["servers_removed"] == 1
    assert payload["totals"]["tier_changes"] == 1
    print("PASS")