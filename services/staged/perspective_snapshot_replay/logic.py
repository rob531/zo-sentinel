"""logic.py -- the service's data/computation layer for perspective replay.

The service reads the authoritative application models and computes the
membership transitions between persisted perspective snapshots.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


def _member_ids(membership: Any) -> Set[str]:
    """Extract server identifiers from the supported membership JSON shapes."""
    value = membership

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]

    if value is None:
        return set()

    if isinstance(value, dict):
        for key in ("members", "member_ids", "server_ids", "servers"):
            candidate = value.get(key)
            if isinstance(candidate, (list, tuple, set)):
                value = candidate
                break
        else:
            value = list(value.keys())

    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item is not None}

    if isinstance(value, (str, int, float, bool)):
        return {str(value)}

    return set()


def _snapshot_members(snapshot: PerspectiveSnapshot) -> Set[str]:
    return _member_ids(snapshot.membership)


def get_perspective_replay(
    db: Session, perspective_id: int
) -> Optional[Dict[str, Any]]:
    """Build the ordered snapshot replay for one perspective.

    ``None`` is returned when the perspective does not exist.  Membership is
    taken from the JSON payload on each snapshot; the event table is read as
    part of the same perspective scope but does not override snapshot truth.
    """
    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if perspective is None:
        return None

    snapshots = db.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at, PerspectiveSnapshot.id)
    ).scalars().all()

    # Events belong to the requested perspective as well.  Snapshot membership
    # remains the source of truth for the replay because it records the
    # complete membership at each observation point.
    db.execute(
        select(PerspectiveEvent).where(
            PerspectiveEvent.perspective_id == perspective_id
        )
    ).scalars().all()

    result: List[Dict[str, Any]] = []
    previous: Optional[Set[str]] = None
    total_changes = 0

    for snapshot in snapshots:
        current = _snapshot_members(snapshot)
        if previous is None:
            added: List[str] = []
            removed: List[str] = []
        else:
            added = sorted(current - previous)
            removed = sorted(previous - current)
            total_changes += len(added) + len(removed)

        result.append(
            {
                "taken_at": snapshot.taken_at,
                "member_count": len(current),
                "added": added,
                "removed": removed,
            }
        )
        previous = current

    return {
        "perspective_id": perspective.id,
        "name": perspective.name,
        "snapshots": result,
        "total_changes": total_changes,
        "first_taken_at": result[0]["taken_at"] if result else None,
        "last_taken_at": result[-1]["taken_at"] if result else None,
    }


def build_replay(db: Session, perspective_id: int) -> Optional[Dict[str, Any]]:
    """Alias for the perspective replay computation."""
    return get_perspective_replay(db, perspective_id)


def replay_perspective(db: Session, perspective_id: int) -> Optional[Dict[str, Any]]:
    """Alias for the perspective replay computation."""
    return get_perspective_replay(db, perspective_id)


def get_replay(db: Session, perspective_id: int) -> Optional[Dict[str, Any]]:
    """Alias for the perspective replay computation."""
    return get_perspective_replay(db, perspective_id)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(test_engine)
    PerspectiveSnapshot.__table__.create(test_engine)
    PerspectiveEvent.__table__.create(test_engine)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_db = TestSession()
    try:
        now = datetime.now(timezone.utc)
        perspective = Perspective(
            id=1,
            name="Example perspective",
            created_by="self-test",
            description="",
            facet_filters={},
        )
        test_db.add(perspective)
        test_db.add_all(
            [
                PerspectiveSnapshot(
                    id=1,
                    perspective_id=1,
                    taken_at=now,
                    membership=["a", "b"],
                ),
                PerspectiveSnapshot(
                    id=2,
                    perspective_id=1,
                    taken_at=now + timedelta(minutes=1),
                    membership=["b", "c"],
                ),
                PerspectiveSnapshot(
                    id=3,
                    perspective_id=1,
                    taken_at=now + timedelta(minutes=2),
                    membership=["c"],
                ),
            ]
        )
        test_db.commit()

        that_app = FastAPI()

        @that_app.get("/api/perspectives/{perspective_id}/replay")
        def replay_route(
            perspective_id: int, db: Session = Depends(get_session)
        ) -> Dict[str, Any]:
            result = get_perspective_replay(db, perspective_id)
            if result is None:
                return {"detail": "Perspective not found"}
            return result

        that_app.dependency_overrides[get_session] = lambda: test_db
        response = TestClient(that_app).get("/api/perspectives/1/replay")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["snapshots"]) == 3
        assert payload["snapshots"][1]["removed"] == ["a"]
        assert payload["snapshots"][1]["added"] == ["c"]
        print("PASS")
    finally:
        test_db.close()
        test_engine.dispose()