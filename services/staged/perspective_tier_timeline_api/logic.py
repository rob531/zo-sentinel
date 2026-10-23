from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent

__all__ = [
    "Perspective",
    "PerspectiveEvent",
    "get_perspective_timeline",
    "get_timeline",
]

MAX_LIMIT = 1000
DEFAULT_LIMIT = 200


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def get_perspective_timeline(
    perspective_id: int,
    limit: int = DEFAULT_LIMIT,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    limit = _bounded_limit(limit)

    perspective_result = db.execute(
        text("SELECT id, name FROM perspectives WHERE id = :perspective_id"),
        {"perspective_id": perspective_id},
    )
    perspective = perspective_result.mappings().first()
    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    event_result = db.execute(
        text(
            """
            SELECT id, perspective_id, server_id, change_type,
                   old_tier, new_tier, seen, created_at
            FROM perspective_events
            WHERE perspective_id = :perspective_id
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        {"perspective_id": perspective_id, "limit": limit},
    )

    transition_result = db.execute(
        text(
            """
            SELECT old_tier, new_tier, COUNT(*) AS count
            FROM perspective_events
            WHERE perspective_id = :perspective_id
            GROUP BY old_tier, new_tier
            ORDER BY old_tier, new_tier
            """
        ),
        {"perspective_id": perspective_id},
    )

    events = [
        {
            "created_at": row["created_at"],
            "server_id": row["server_id"],
            "change_type": row["change_type"],
            "old_tier": row["old_tier"],
            "new_tier": row["new_tier"],
            "seen": row["seen"],
        }
        for row in event_result.mappings()
    ]

    transition_counts = {
        f"{row['old_tier']}->{row['new_tier']}": int(row["count"])
        for row in transition_result.mappings()
    }

    return {
        "perspective_id": perspective["id"],
        "perspective_name": perspective["name"],
        "events": events,
        "transition_counts": transition_counts,
    }


get_timeline = get_perspective_timeline


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

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
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
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
                    server_id INTEGER,
                    change_type TEXT,
                    old_tier TEXT,
                    new_tier TEXT,
                    seen BOOLEAN,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text("INSERT INTO perspectives (id, name) VALUES (:id, :name)"),
            {"id": 1, "name": "Seeded Perspective"},
        )
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
            {
                "id": 1,
                "perspective_id": 1,
                "server_id": 10,
                "change_type": "TIER_CHANGED",
                "old_tier": "TRUSTED_GENERAL",
                "new_tier": "CAUTION_LIMITED",
                "seen": False,
                "created_at": "2024-01-02T00:00:00",
            },
        )
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
            {
                "id": 2,
                "perspective_id": 1,
                "server_id": 10,
                "change_type": "TIER_CHANGED",
                "old_tier": "CAUTION_LIMITED",
                "new_tier": "HIGH_RISK_ISOLATED",
                "seen": True,
                "created_at": "2024-01-01T00:00:00",
            },
        )

    local_session = Session(engine)
    that_app = FastAPI()

    @that_app.get("/api/perspectives/{perspective_id}/timeline")
    def timeline(
        perspective_id: int,
        limit: int = DEFAULT_LIMIT,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_perspective_timeline(
            perspective_id=perspective_id,
            limit=limit,
            db=db,
        )

    def override_get_session():
        yield local_session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/1/timeline")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["events"]) == 2
        assert (
            payload["transition_counts"]["TRUSTED_GENERAL->CAUTION_LIMITED"] == 1
        )
        assert payload["perspective_name"] == "Seeded Perspective"

    local_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()