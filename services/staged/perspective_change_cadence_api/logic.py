from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


def get_change_cadence(session: Session, days: int = 30) -> dict[str, Any]:
    """Return perspective change-event statistics for the requested UTC window."""
    if days < 0:
        raise ValueError("days must be non-negative")

    since = datetime.now(timezone.utc) - timedelta(days=days)

    event_rows = session.execute(
        text(
            """
            SELECT
                pe.id,
                pe.perspective_id,
                pe.change_type,
                pe.old_tier,
                pe.new_tier,
                pe.seen,
                p.name
            FROM perspective_events AS pe
            LEFT JOIN perspectives AS p
                ON p.id = pe.perspective_id
            WHERE pe.created_at >= :since
            ORDER BY pe.id
            """
        ),
        {"since": since},
    ).mappings()

    perspective_rows = session.execute(
        select(Perspective.id, Perspective.name).order_by(Perspective.id)
    ).all()

    perspectives: dict[str, dict[str, Any]] = {}
    for perspective_id, name in perspective_rows:
        perspectives[perspective_id] = {
            "perspective_id": perspective_id,
            "name": name,
            "event_count": 0,
            "by_change_type": {},
            "tier_transitions": [],
            "unseen_count": 0,
        }

    change_type_counts: dict[str, Counter[str]] = {}
    transition_counts: dict[str, Counter[tuple[str, str]]] = {}
    total_events = 0

    for row in event_rows:
        total_events += 1
        perspective_id = row["perspective_id"]

        if perspective_id not in perspectives:
            perspectives[perspective_id] = {
                "perspective_id": perspective_id,
                "name": row["name"],
                "event_count": 0,
                "by_change_type": {},
                "tier_transitions": [],
                "unseen_count": 0,
            }

        perspective = perspectives[perspective_id]
        perspective["event_count"] += 1

        change_type = row["change_type"]
        perspective_change_types = change_type_counts.setdefault(
            perspective_id, Counter()
        )
        perspective_change_types[change_type] += 1

        old_tier = row["old_tier"] or ""
        new_tier = row["new_tier"] or ""
        perspective_transitions = transition_counts.setdefault(
            perspective_id, Counter()
        )
        perspective_transitions[(old_tier, new_tier)] += 1

        if not row["seen"]:
            perspective["unseen_count"] += 1

    for perspective_id, perspective in perspectives.items():
        perspective["by_change_type"] = dict(
            sorted(change_type_counts.get(perspective_id, Counter()).items())
        )
        perspective["tier_transitions"] = [
            {
                "old_tier": old_tier,
                "new_tier": new_tier,
                "count": count,
            }
            for (old_tier, new_tier), count in sorted(
                transition_counts.get(perspective_id, Counter()).items()
            )
        ]

    return {
        "days": days,
        "total_events": total_events,
        "perspectives": list(perspectives.values()),
    }


def get_perspective_change_cadence(
    session: Session, days: int = 30
) -> dict[str, Any]:
    return get_change_cadence(session, days)


def compute_change_cadence(session: Session, days: int = 30) -> dict[str, Any]:
    return get_change_cadence(session, days)


def compute_perspective_change_cadence(
    session: Session, days: int = 30
) -> dict[str, Any]:
    return get_change_cadence(session, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with test_session_factory() as session:
        session.add_all(
            [
                Perspective(
                    id="perspective-1",
                    name="First Perspective",
                    created_by="self-test",
                    description="",
                    facet_filters="{}",
                ),
                Perspective(
                    id="perspective-2",
                    name="Second Perspective",
                    created_by="self-test",
                    description="",
                    facet_filters="{}",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="upgrade",
                    old_tier="standard",
                    new_tier="trusted",
                    seen=True,
                    created_at=now - timedelta(hours=2),
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-1",
                    server_id="server-2",
                    change_type="review",
                    old_tier="standard",
                    new_tier="trusted",
                    seen=False,
                    created_at=now - timedelta(hours=1),
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id="perspective-2",
                    server_id="server-3",
                    change_type="upgrade",
                    old_tier="limited",
                    new_tier="standard",
                    seen=True,
                    created_at=now - timedelta(days=40),
                ),
                PerspectiveEvent(
                    id=4,
                    perspective_id="perspective-2",
                    server_id="server-4",
                    change_type="downgrade",
                    old_tier="standard",
                    new_tier="limited",
                    seen=True,
                    created_at=now - timedelta(days=50),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/perspectives/change-cadence")
    def change_cadence_endpoint(
        days: int = 30,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_change_cadence(session, days)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/perspectives/change-cadence", params={"days": 30}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        assert payload["total_events"] == 2
        assert len(payload["perspectives"]) == 2

        first = next(
            item
            for item in payload["perspectives"]
            if item["perspective_id"] == "perspective-1"
        )
        second = next(
            item
            for item in payload["perspectives"]
            if item["perspective_id"] == "perspective-2"
        )
        assert first["name"] == "First Perspective"
        assert first["event_count"] == 2
        assert first["unseen_count"] == 1
        assert {
            "old_tier": "standard",
            "new_tier": "trusted",
            "count": 2,
        } in first["tier_transitions"]
        assert second["event_count"] == 0
        assert second["unseen_count"] == 0

    print("PASS")