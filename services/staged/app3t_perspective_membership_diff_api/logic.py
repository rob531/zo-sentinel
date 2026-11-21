"""logic.py -- perspective membership diff computation over the real app models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


def _membership_entries(membership: Any) -> Dict[str, Any]:
    """Return a normalized server-id to tier mapping for supported JSON shapes."""
    if membership is None:
        return {}
    if isinstance(membership, str):
        import json

        try:
            membership = json.loads(membership)
        except (TypeError, ValueError):
            return {}

    if isinstance(membership, dict):
        entries: Dict[str, Any] = {}
        for server_id, value in membership.items():
            entries[str(server_id)] = _extract_tier(value)
        return entries

    if isinstance(membership, list):
        entries = {}
        for value in membership:
            if isinstance(value, dict):
                server_id = value.get("server_id", value.get("id"))
                if server_id is not None:
                    entries[str(server_id)] = _extract_tier(value)
            elif isinstance(value, (str, int)):
                entries[str(value)] = None
        return entries

    return {}


def _extract_tier(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("tier", "risk_tier", "new_tier", "old_tier"):
            if value.get(key) is not None:
                return value[key]
        return None
    return value


def _event_tiers(events: List[PerspectiveEvent]) -> Dict[str, Tuple[Any, Any]]:
    result: Dict[str, Tuple[Any, Any]] = {}
    for event in events:
        if event.server_id is None:
            continue
        result[str(event.server_id)] = (event.old_tier, event.new_tier)
    return result


def get_membership_diff(
    db: Session, perspective_id: int
) -> Optional[Dict[str, Any]]:
    """Compare the two most recent snapshots for a perspective.

    ``None`` is returned when the perspective does not exist. A perspective
    with fewer than two snapshots is represented by an empty diff so that the
    router can distinguish it from an unknown perspective.
    """
    perspective = db.get(Perspective, perspective_id)
    if perspective is None:
        return None

    snapshots = db.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
    ).scalars().all()

    if len(snapshots) < 2:
        return {
            "perspective_id": perspective.id,
            "name": perspective.name,
            "from_taken_at": None,
            "to_taken_at": None,
            "added": [],
            "removed": [],
            "unchanged_count": 0,
            "tier_changes": [],
        }

    newer, older = snapshots[0], snapshots[1]
    new_membership = _membership_entries(newer.membership)
    old_membership = _membership_entries(older.membership)

    new_ids = set(new_membership)
    old_ids = set(old_membership)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    common_ids = new_ids & old_ids

    events = db.execute(
        select(PerspectiveEvent)
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.asc(), PerspectiveEvent.id.asc())
    ).scalars().all()
    event_tiers = _event_tiers(events)

    tier_changes: List[Dict[str, Any]] = []
    for server_id in sorted(common_ids):
        old_tier = old_membership.get(server_id)
        new_tier = new_membership.get(server_id)
        event_old, event_new = event_tiers.get(server_id, (None, None))

        if old_tier is None and event_old is not None:
            old_tier = event_old
        if new_tier is None and event_new is not None:
            new_tier = event_new

        if old_tier != new_tier:
            tier_changes.append(
                {
                    "server_id": server_id,
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                }
            )

    return {
        "perspective_id": perspective.id,
        "name": perspective.name,
        "from_taken_at": older.taken_at,
        "to_taken_at": newer.taken_at,
        "added": added,
        "removed": removed,
        "unchanged_count": len(common_ids) - len(tier_changes),
        "tier_changes": tier_changes,
    }


def compute_membership_diff(
    db: Session, perspective_id: int
) -> Optional[Dict[str, Any]]:
    """Compatibility alias for callers using the computation-oriented name."""
    return get_membership_diff(db, perspective_id)


def membership_diff(
    db: Session, perspective_id: int
) -> Optional[Dict[str, Any]]:
    """Compatibility alias for callers using the short function name."""
    return get_membership_diff(db, perspective_id)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Perspective.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)

    db = SessionLocal()
    db.add(
        Perspective(
            id=1,
            name="Contract Perspective",
            created_by="self-test",
            facet_filters={},
        )
    )
    db.add(
        PerspectiveSnapshot(
            id=1,
            perspective_id=1,
            membership={"server-1": "A", "server-2": "B", "server-3": "C"},
            taken_at=datetime(2024, 1, 1, 12, 0, 0),
        )
    )
    db.add(
        PerspectiveSnapshot(
            id=2,
            perspective_id=1,
            membership={"server-1": "B", "server-2": "B", "server-3": "C", "server-4": "A"},
            taken_at=datetime(2024, 1, 2, 12, 0, 0),
        )
    )
    db.commit()
    db.close()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/app3t/perspectives/{perspective_id}/membership-diff")
    def membership_diff_endpoint(
        perspective_id: int,
        session: Session = Depends(get_session),
    ):
        result = get_membership_diff(session, perspective_id)
        if result is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Perspective not found")
        return result

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/app3t/perspectives/1/membership-diff")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["added"]) == 1
    assert payload["added"] == ["server-4"]
    assert payload["removed"] == []
    assert payload["tier_changes"] == [
        {"server_id": "server-1", "old_tier": "A", "new_tier": "B"}
    ]

    missing = client.get("/api/app3t/perspectives/999/membership-diff")
    assert missing.status_code == 404
    print("PASS")