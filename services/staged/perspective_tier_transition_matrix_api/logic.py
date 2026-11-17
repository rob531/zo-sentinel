from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


def _event_sort_key(value: Any) -> tuple:
    return (value is None, str(value))


def get_tier_transition_matrix(
    perspective_id: Optional[str] = None,
    days: int = 30,
    db: Session = Depends(get_session),
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    days = max(0, int(days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = select(PerspectiveEvent).where(PerspectiveEvent.created_at >= cutoff)
    if perspective_id is not None:
        statement = statement.where(PerspectiveEvent.perspective_id == perspective_id)

    statement = statement.order_by(
        PerspectiveEvent.created_at.asc(),
        PerspectiveEvent.id.asc(),
    )
    events = list(db.execute(statement).scalars())

    grouped: Dict[Any, List[PerspectiveEvent]] = {}
    for event in events:
        grouped.setdefault(event.perspective_id, []).append(event)

    def build_result(group_id: Any, group_events: List[PerspectiveEvent]) -> Dict[str, Any]:
        transitions: Dict[tuple, int] = {}
        unseen_count = 0
        servers = set()

        for event in group_events:
            transition = (event.old_tier, event.new_tier)
            transitions[transition] = transitions.get(transition, 0) + 1

            if event.seen is False:
                unseen_count += 1
            if event.server_id is not None:
                servers.add(event.server_id)

        transition_rows = [
            {"old_tier": old_tier, "new_tier": new_tier, "count": count}
            for (old_tier, new_tier), count in sorted(
                transitions.items(),
                key=lambda item: (
                    _event_sort_key(item[0][0]),
                    _event_sort_key(item[0][1]),
                ),
            )
        ]

        return {
            "perspective_id": group_id,
            "days": days,
            "transitions": transition_rows,
            "unseen_count": unseen_count,
            "servers_affected": len(servers),
            "total_events": len(group_events),
        }

    if perspective_id is not None:
        return build_result(perspective_id, grouped.get(perspective_id, []))

    return [
        build_result(group_id, grouped[group_id])
        for group_id in sorted(grouped, key=_event_sort_key)
    ]


get_tier_transitions = get_tier_transition_matrix
calculate_tier_transition_matrix = get_tier_transition_matrix


if __name__ == "__main__":
    from datetime import datetime as datetime_type

    from fastapi import FastAPI
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
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime_type.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="medium",
                    seen=True,
                    created_at=now - timedelta(days=1),
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-1",
                    server_id="server-2",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="high",
                    seen=False,
                    created_at=now - timedelta(days=2),
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id="perspective-2",
                    server_id="server-3",
                    change_type="tier_change",
                    old_tier="medium",
                    new_tier="low",
                    seen=True,
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/perspectives/tier-transitions")
    def tier_transitions(
        perspective_id: Optional[str] = None,
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_tier_transition_matrix(perspective_id, days, session)

    with TestClient(test_app) as client:
        response = client.get(
            "/api/perspectives/tier-transitions",
            params={"perspective_id": "perspective-1", "days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["perspective_id"] == "perspective-1"
        assert any(item["count"] == 1 for item in payload["transitions"])
        assert payload["unseen_count"] == 1
        assert payload["total_events"] == 2

    print("PASS")