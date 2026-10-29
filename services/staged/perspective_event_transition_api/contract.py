from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


class ChangeTypeCount(BaseModel):
    change_type: str
    count: int


class TierTransition(BaseModel):
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int


class ServerTransitionCount(BaseModel):
    server_id: str
    count: int


class EventTransitionResponse(BaseModel):
    days: int
    events: int
    by_change_type: list[ChangeTypeCount]
    transitions: list[TierTransition]
    unseen: int
    top_servers: list[ServerTransitionCount]


def get_event_transitions(
    perspective_id: Optional[str],
    days: int,
    session: Session = Depends(get_session),
) -> EventTransitionResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = (
        __import__("sqlalchemy")
        .select(PerspectiveEvent, Perspective.name)
        .join(Perspective, Perspective.id == PerspectiveEvent.perspective_id)
        .where(PerspectiveEvent.created_at >= cutoff)
    )
    if perspective_id is not None:
        statement = statement.where(PerspectiveEvent.perspective_id == perspective_id)

    rows = session.execute(statement).all()

    change_type_counts: Counter[str] = Counter()
    transition_counts: Counter[tuple[Optional[str], Optional[str]]] = Counter()
    server_counts: Counter[str] = Counter()
    unseen = 0

    for event, _perspective_name in rows:
        change_type_counts[str(event.change_type)] += 1
        transition_counts[(event.old_tier, event.new_tier)] += 1
        server_counts[str(event.server_id)] += 1
        if not event.seen:
            unseen += 1

    by_change_type = [
        {"change_type": change_type, "count": count}
        for change_type, count in sorted(
            change_type_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    transitions = [
        {"old_tier": old_tier, "new_tier": new_tier, "count": count}
        for (old_tier, new_tier), count in sorted(
            transition_counts.items(),
            key=lambda item: (
                -item[1],
                item[0][0] is not None,
                item[0][0] or "",
                item[0][1] is not None,
                item[0][1] or "",
            ),
        )
    ]
    top_servers = [
        {"server_id": server_id, "count": count}
        for server_id, count in sorted(
            server_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    return EventTransitionResponse(
        days=days,
        events=len(rows),
        by_change_type=by_change_type,
        transitions=transitions,
        unseen=unseen,
        top_servers=top_servers,
    )


def create_app() -> FastAPI:
    application = FastAPI(title="Perspective Event Transition API")

    @application.get(
        "/api/perspectives/event-transitions",
        response_model=EventTransitionResponse,
    )
    def event_transitions(
        perspective_id: Optional[str] = Query(default=None),
        days: int = Query(default=30),
        session: Session = Depends(get_session),
    ) -> EventTransitionResponse:
        return get_event_transitions(
            perspective_id=perspective_id,
            days=days,
            session=session,
        )

    return application


app = create_app()


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id TEXT PRIMARY KEY,
                    perspective_id TEXT NOT NULL,
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
        connection.execute(
            text(
                "INSERT INTO perspectives (id, name) VALUES (:id, :name)"
            ),
            [
                {"id": "perspective-1", "name": "First Perspective"},
                {"id": "perspective-2", "name": "Second Perspective"},
            ],
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO perspective_events (
                    id, perspective_id, server_id, change_type,
                    old_tier, new_tier, seen, created_at
                ) VALUES (
                    :id, :perspective_id, :server_id, :change_type,
                    :old_tier, :new_tier, :seen, :created_at
                )
                """
            ),
            [
                {
                    "id": "event-1",
                    "perspective_id": "perspective-1",
                    "server_id": "server-1",
                    "change_type": "tier_change",
                    "old_tier": "low",
                    "new_tier": "high",
                    "seen": 0,
                    "created_at": now,
                },
                {
                    "id": "event-2",
                    "perspective_id": "perspective-1",
                    "server_id": "server-2",
                    "change_type": "metadata_change",
                    "old_tier": None,
                    "new_tier": None,
                    "seen": 1,
                    "created_at": now,
                },
                {
                    "id": "event-3",
                    "perspective_id": "perspective-2",
                    "server_id": "server-1",
                    "change_type": "tier_change",
                    "old_tier": "medium",
                    "new_tier": "high",
                    "seen": 1,
                    "created_at": now,
                },
            ],
        )

    SessionLocal = Session
    local_app = create_app()

    def override_get_session():
        session = SessionLocal(bind=engine)
        try:
            yield session
        finally:
            session.close()

    local_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(local_app) as client:
            response = client.get(
                "/api/perspectives/event-transitions",
                params={"days": 30},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["events"] == 3, payload
            assert any(
                transition["old_tier"] == "low"
                and transition["new_tier"] == "high"
                for transition in payload["transitions"]
            ), payload
            assert payload["unseen"] == 1, payload
    finally:
        local_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


def _run_self_test() -> None:
    run_self_test()


def main() -> None:
    run_self_test()


if __name__ == "__main__":
    main()