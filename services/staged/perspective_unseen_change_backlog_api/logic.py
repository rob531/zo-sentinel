from fastapi import APIRouter, Depends, Query

from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter()


@router.get("/perspectives/changes/unseen")
def get_unseen_changes(
    limit: int = Query(default=100, ge=1, le=500),
    session=Depends(get_session),
):
    events = (
        session.query(PerspectiveEvent)
        .filter(PerspectiveEvent.seen.is_(False))
        .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
        .limit(limit)
        .all()
    )

    return {
        "count": len(events),
        "events": [
            {
                "id": event.id,
                "perspective_id": event.perspective_id,
                "server_id": event.server_id,
                "change_type": event.change_type,
                "old_tier": event.old_tier,
                "new_tier": event.new_tier,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }


if __name__ == "__main__":
    from datetime import datetime, timedelta

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

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id INTEGER PRIMARY KEY,
                    perspective_id VARCHAR NOT NULL,
                    server_id VARCHAR NOT NULL,
                    change_type VARCHAR NOT NULL,
                    old_tier VARCHAR NOT NULL,
                    new_tier VARCHAR NOT NULL,
                    seen BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

        now = datetime(2024, 1, 1, 12, 0, 0)
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
                    "id": 1,
                    "perspective_id": "perspective-a",
                    "server_id": "server-a",
                    "change_type": "tier_changed",
                    "old_tier": "low",
                    "new_tier": "medium",
                    "seen": False,
                    "created_at": now,
                },
                {
                    "id": 2,
                    "perspective_id": "perspective-a",
                    "server_id": "server-b",
                    "change_type": "tier_changed",
                    "old_tier": "medium",
                    "new_tier": "high",
                    "seen": True,
                    "created_at": now + timedelta(minutes=1),
                },
                {
                    "id": 3,
                    "perspective_id": "perspective-b",
                    "server_id": "server-c",
                    "change_type": "tier_changed",
                    "old_tier": "low",
                    "new_tier": "high",
                    "seen": False,
                    "created_at": now + timedelta(minutes=2),
                },
            ],
        )

    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/perspectives/changes/unseen")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert [event["id"] for event in payload["events"]] == [3, 1]
    assert all(event["id"] != 2 for event in payload["events"])
    assert payload["events"][0]["created_at"] >= payload["events"][1]["created_at"]

    print("PASS")