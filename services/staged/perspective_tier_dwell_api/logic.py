from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot


def _membership_server_ids(value: Any) -> set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {value}

    if isinstance(value, dict):
        for key in ("server_id", "serverId", "id"):
            item = value.get(key)
            if isinstance(item, str):
                return {item}

        for key in ("servers", "members", "membership"):
            if key in value:
                return _membership_server_ids(value[key])
        return set()

    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for item in value:
            result.update(_membership_server_ids(item))
        return result

    return set()


def _event_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def compute_tier_dwell(
    perspective_id: Any = None,
    db: Session | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if db is None:
        db = kwargs.pop("session", None)
    if perspective_id is None:
        perspective_id = kwargs.pop("perspective", None)

    if db is not None and not isinstance(perspective_id, (str, int)):
        perspective_id, db = db, perspective_id

    if db is None:
        raise TypeError("A database session is required")
    if perspective_id is None:
        raise TypeError("A perspective_id is required")

    events = list(
        db.execute(
            select(PerspectiveEvent)
            .where(PerspectiveEvent.perspective_id == perspective_id)
            .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
        )
        .scalars()
        .all()
    )

    snapshot = db.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc(), PerspectiveSnapshot.id.desc())
    ).scalars().first()

    transitions: Counter[tuple[Any, Any]] = Counter()
    unseen_events = 0
    event_servers: set[str] = set()

    for event in events:
        if event.seen is not True:
            unseen_events += 1
        if event.server_id is not None:
            event_servers.add(str(event.server_id))
        if event.old_tier is not None and event.new_tier is not None:
            transitions[(event.old_tier, event.new_tier)] += 1

    events_by_server: dict[str, list[datetime]] = {}
    for event in events:
        if event.server_id is None:
            continue
        created_at = _event_time(event.created_at)
        if created_at is not None:
            events_by_server.setdefault(str(event.server_id), []).append(created_at)

    dwell_intervals: list[float] = []
    for timestamps in events_by_server.values():
        ordered = sorted(timestamps)
        for previous, current in zip(ordered, ordered[1:]):
            dwell_intervals.append((current - previous).total_seconds() / 86400.0)

    avg_dwell_days = (
        round(sum(dwell_intervals) / len(dwell_intervals), 4)
        if dwell_intervals
        else 0.0
    )

    if not event_servers and snapshot is not None:
        servers_affected = len(_membership_server_ids(snapshot.membership))
    else:
        servers_affected = len(event_servers)

    return {
        "perspective_id": perspective_id,
        "total_events": len(events),
        "unseen_events": unseen_events,
        "transitions": [
            {
                "old_tier": old_tier,
                "new_tier": new_tier,
                "count": count,
            }
            for (old_tier, new_tier), count in sorted(
                transitions.items(),
                key=lambda item: (str(item[0][0]), str(item[0][1])),
            )
        ],
        "avg_dwell_days": avg_dwell_days,
        "servers_affected": servers_affected,
    }


def get_tier_dwell(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return compute_tier_dwell(*args, **kwargs)


def get_tier_dwell_data(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return compute_tier_dwell(*args, **kwargs)


def get_perspective_tier_dwell(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return compute_tier_dwell(*args, **kwargs)


def calculate_tier_dwell(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return compute_tier_dwell(*args, **kwargs)


def _self_test() -> None:
    from datetime import timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    testing_session.add_all(
        [
            PerspectiveEvent(
                perspective_id="perspective-test",
                server_id="server-1",
                change_type="tier_change",
                old_tier="low",
                new_tier="medium",
                seen=False,
                created_at=base_time,
            ),
            PerspectiveEvent(
                perspective_id="perspective-test",
                server_id="server-1",
                change_type="tier_change",
                old_tier="medium",
                new_tier="high",
                seen=True,
                created_at=base_time + timedelta(days=2),
            ),
            PerspectiveEvent(
                perspective_id="perspective-test",
                server_id="server-2",
                change_type="tier_change",
                old_tier="low",
                new_tier="high",
                seen=True,
                created_at=base_time + timedelta(days=1),
            ),
        ]
    )
    testing_session.add(
        PerspectiveSnapshot(
            perspective_id="perspective-test",
            taken_at=base_time + timedelta(days=2),
            membership={"servers": ["server-1", "server-2"]},
        )
    )
    testing_session.commit()

    local_app = FastAPI()

    @local_app.get("/api/perspectives/{perspective_id}/tier-dwell")
    def tier_dwell_endpoint(
        perspective_id: str,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return compute_tier_dwell(perspective_id, db)

    def override_get_session() -> Any:
        yield testing_session

    local_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(local_app) as client:
            response = client.get(
                "/api/perspectives/perspective-test/tier-dwell"
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_events"] == 3
            assert len(payload["transitions"]) >= 1
            assert payload["unseen_events"] == 1
            assert payload["servers_affected"] == 2
            assert payload["avg_dwell_days"] == 2.0
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()