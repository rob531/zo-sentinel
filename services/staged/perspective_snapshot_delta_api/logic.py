from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _server_ids(membership: Any) -> list[Any]:
    if isinstance(membership, str):
        try:
            membership = json.loads(membership)
        except (TypeError, ValueError):
            return []

    if isinstance(membership, dict):
        membership = membership.get("server_ids", membership.get("servers", []))

    if not isinstance(membership, list):
        return []

    result: list[Any] = []
    for item in membership:
        if isinstance(item, dict):
            item = item.get("server_id", item.get("id"))
        if item is not None:
            result.append(item)
    return result


def _sorted_ids(values: Iterable[Any]) -> list[Any]:
    values = list(values)
    try:
        return sorted(values)
    except TypeError:
        return sorted(values, key=lambda value: (type(value).__name__, str(value)))


def get_perspective_snapshot_delta(
    days: int,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    snapshot_rows = db.execute(
        select(PerspectiveSnapshot).order_by(
            PerspectiveSnapshot.perspective_id,
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
    ).scalars()

    latest: dict[int, list[PerspectiveSnapshot]] = {}
    for snapshot in snapshot_rows:
        rows = latest.setdefault(snapshot.perspective_id, [])
        if len(rows) < 2:
            rows.append(snapshot)

    perspective_ids = [
        perspective_id
        for perspective_id, rows in latest.items()
        if rows and (_as_utc(rows[0].taken_at) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    ]
    perspective_ids.sort()

    perspectives: list[dict[str, Any]] = []
    added_total = 0
    removed_total = 0

    if perspective_ids:
        perspective_rows = db.execute(
            select(Perspective).where(Perspective.id.in_(perspective_ids))
        ).scalars()
        perspective_by_id = {perspective.id: perspective for perspective in perspective_rows}

        event_rows = db.execute(
            select(PerspectiveEvent).where(
                PerspectiveEvent.perspective_id.in_(perspective_ids)
            )
        ).scalars()

        events_by_perspective: dict[int, list[PerspectiveEvent]] = {
            perspective_id: [] for perspective_id in perspective_ids
        }
        for event in event_rows:
            if event.perspective_id in events_by_perspective:
                events_by_perspective[event.perspective_id].append(event)

        for perspective_id in perspective_ids:
            snapshots = latest[perspective_id]
            perspective = perspective_by_id.get(perspective_id)
            if perspective is None or len(snapshots) < 2:
                continue

            previous_ids = set(_server_ids(snapshots[1].membership))
            current_ids = set(_server_ids(snapshots[0].membership))

            added = _sorted_ids(current_ids - previous_ids)
            removed = _sorted_ids(previous_ids - current_ids)
            unchanged = len(current_ids & previous_ids)

            counts: Counter[str] = Counter()
            for event in events_by_perspective[perspective_id]:
                event_time = _as_utc(event.created_at)
                if event_time is not None and event_time < cutoff:
                    continue
                change_type = event.change_type
                counts[str(change_type) if change_type is not None else "unknown"] += 1

            perspectives.append(
                {
                    "perspective_id": perspective_id,
                    "name": perspective.name,
                    "last_taken_at": snapshots[0].taken_at,
                    "added": added,
                    "removed": removed,
                    "unchanged": unchanged,
                    "event_counts": dict(counts),
                }
            )
            added_total += len(added)
            removed_total += len(removed)

    return {
        "days": days,
        "perspectives": perspectives,
        "totals": {
            "added": added_total,
            "removed": removed_total,
            "perspectives": len(perspectives),
        },
    }


get_snapshot_delta = get_perspective_snapshot_delta
calculate_snapshot_delta = get_perspective_snapshot_delta
list_snapshot_deltas = get_perspective_snapshot_delta
list_perspective_snapshot_deltas = get_perspective_snapshot_delta


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER,
                    name TEXT NOT NULL,
                    description TEXT,
                    facet_filters TEXT,
                    created_by INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_snapshots (
                    id INTEGER PRIMARY KEY,
                    perspective_id INTEGER NOT NULL,
                    taken_at DATETIME NOT NULL,
                    membership JSON NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id INTEGER PRIMARY KEY,
                    perspective_id INTEGER NOT NULL,
                    server_id TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    old_tier TEXT,
                    new_tier TEXT,
                    seen INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO perspectives
                    (id, org_id, name, description, facet_filters, created_by, created_at, updated_at)
                VALUES
                    (1, 1, 'Production', NULL, NULL, 1, :now, :now)
                """
            ),
            {"now": now.isoformat()},
        )
        connection.execute(
            text(
                """
                INSERT INTO perspective_snapshots
                    (id, perspective_id, taken_at, membership)
                VALUES
                    (1, 1, :old_time, :old_membership),
                    (2, 1, :new_time, :new_membership)
                """
            ),
            {
                "old_time": (now - timedelta(days=1)).isoformat(),
                "new_time": now.isoformat(),
                "old_membership": json.dumps(["server-a", "server-b"]),
                "new_membership": json.dumps(["server-a", "server-c"]),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO perspective_events
                    (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
                VALUES
                    (1, 1, 'server-b', 'removed', 'preferred', NULL, 0, :now),
                    (2, 1, 'server-c', 'added', NULL, 'preferred', 0, :now)
                """
            ),
            {"now": now.isoformat()},
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/perspectives/snapshots/delta")
    def snapshot_delta(days: int = 30, db: Session = Depends(get_session)):
        return get_perspective_snapshot_delta(days=days, db=db)

    response = TestClient(test_app).get(
        "/api/perspectives/snapshots/delta",
        params={"days": 30},
    )
    payload = response.json()

    assert response.status_code == 200
    assert len(payload["perspectives"]) == 1
    assert payload["perspectives"][0]["added"] == ["server-c"]
    assert payload["perspectives"][0]["removed"] == ["server-b"]
    assert payload["totals"]["added"] == 1
    assert payload["totals"]["removed"] == 1
    assert payload["totals"]["perspectives"] == 1

    print("PASS")