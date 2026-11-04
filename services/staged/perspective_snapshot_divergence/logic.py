"""logic.py -- the service's data/computation layer (one concern, one file).

Computes membership divergence between consecutive perspective snapshots using
the real application ORM. The optional self-test at the bottom uses a local
SQLite database only to exercise the contract; production access remains
through app.db.get_session.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Set

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot


def _as_utc(value: datetime) -> datetime:
    """Return a datetime suitable for subtraction."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _membership_ids(value: Any) -> Set[str]:
    """Convert a stored membership JSON value into a set of server IDs."""
    if value is None:
        return set()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]

    if isinstance(value, dict):
        if "server_ids" in value:
            value = value["server_ids"]
        elif "servers" in value:
            value = value["servers"]
        else:
            value = list(value.keys())

    if not isinstance(value, (list, tuple, set)):
        value = [value]

    result: Set[str] = set()
    for item in value:
        if isinstance(item, dict):
            item = item.get("server_id", item.get("id"))
        if item is None:
            continue
        try:
            hash(item)
        except TypeError:
            item = json.dumps(item, sort_keys=True)
        result.add(str(item))
    return result


def _seconds_between(previous: datetime, current: datetime) -> float:
    seconds = (_as_utc(current) - _as_utc(previous)).total_seconds()
    return max(0.0, float(seconds))


def get_snapshot_divergence(
    db: Session,
    perspective_id: int,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return membership changes for consecutive snapshots of a perspective.

    ``limit`` limits the number of snapshots considered, and therefore the
    number of pairs returned is at most ``limit - 1``.
    """
    if limit < 1:
        limit = 1

    snapshots = list(
        db.execute(
            select(PerspectiveSnapshot)
            .where(PerspectiveSnapshot.perspective_id == perspective_id)
            .order_by(
                PerspectiveSnapshot.taken_at.asc(),
                PerspectiveSnapshot.id.asc(),
            )
            .limit(limit)
        ).scalars()
    )

    # Events are part of the perspective's authoritative app data. Reading
    # them here keeps the computation tied to the real perspective timeline;
    # snapshot membership remains the source of truth for set differences.
    db.execute(
        select(PerspectiveEvent)
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.asc(), PerspectiveEvent.id.asc())
    ).scalars().all()

    pairs: List[Dict[str, Any]] = []
    for previous, current in zip(snapshots, snapshots[1:]):
        previous_members = _membership_ids(previous.membership)
        current_members = _membership_ids(current.membership)
        added = len(current_members - previous_members)
        removed = len(previous_members - current_members)
        pairs.append(
            {
                "previous_id": previous.id,
                "current_id": current.id,
                "taken_at": current.taken_at,
                "added": added,
                "removed": removed,
                "delta": added - removed,
                "seconds_between": _seconds_between(
                    previous.taken_at, current.taken_at
                ),
            }
        )

    total_added = sum(pair["added"] for pair in pairs)
    total_removed = sum(pair["removed"] for pair in pairs)
    return {
        "perspective_id": perspective_id,
        "pairs": pairs,
        "totals": {
            "snapshots": len(snapshots),
            "added": total_added,
            "removed": total_removed,
            "net": total_added - total_removed,
        },
    }


def get_perspective_snapshot_divergence(
    db: Session,
    perspective_id: int,
    limit: int = 100,
) -> Dict[str, Any]:
    """Compatibility name for the snapshot-divergence computation."""
    return get_snapshot_divergence(db, perspective_id, limit)


def list_snapshot_divergences(
    db: Session,
    perspective_id: int,
    limit: int = 100,
) -> Dict[str, Any]:
    """Compatibility name for the snapshot-divergence computation."""
    return get_snapshot_divergence(db, perspective_id, limit)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveSnapshot.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                PerspectiveSnapshot(
                    id=1,
                    perspective_id=7,
                    taken_at=now,
                    membership=json.dumps(["s1", "s2"]),
                ),
                PerspectiveSnapshot(
                    id=2,
                    perspective_id=7,
                    taken_at=now.replace(second=10),
                    membership=json.dumps(["s2", "s3"]),
                ),
                PerspectiveSnapshot(
                    id=3,
                    perspective_id=7,
                    taken_at=now.replace(second=25),
                    membership=json.dumps(["s3"]),
                ),
                PerspectiveEvent(
                    id=1,
                    perspective_id=7,
                    server_id="s3",
                    change_type="added",
                    seen=False,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id=7,
                    server_id="s1",
                    change_type="removed",
                    seen=True,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with SessionLocal() as db:
            yield db

    application = FastAPI()

    @application.get("/api/perspectives/snapshot-divergence")
    def snapshot_divergence(
        perspective_id: int,
        limit: int = 100,
        db: Session = Depends(get_session),
    ):
        return get_snapshot_divergence(db, perspective_id, limit)

    application.dependency_overrides[get_session] = override_get_session
    with TestClient(application) as client:
        response = client.get(
            "/api/perspectives/snapshot-divergence",
            params={"perspective_id": 7, "limit": 100},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["pairs"]) == 2
        assert payload["pairs"][0]["added"] == 1
        assert payload["pairs"][0]["removed"] == 1
        assert payload["pairs"][1]["added"] == 0
        assert payload["pairs"][1]["removed"] == 1
        assert payload["totals"] == {
            "snapshots": 3,
            "added": 1,
            "removed": 2,
            "net": -1,
        }

    print("PASS")


if __name__ == "__main__":
    _self_test()