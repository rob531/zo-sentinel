from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent
from services.staged.perspective_unseen_event_queue.router import router


_PERSPECTIVE_ID = "perspective-unseen-1"
_SERVER_ID = "server-risk-1"


def _create_test_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    facet_filters JSON NOT NULL,
                    org_id VARCHAR,
                    created_by VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id INTEGER PRIMARY KEY,
                    perspective_id VARCHAR NOT NULL,
                    server_id VARCHAR NOT NULL,
                    change_type VARCHAR NOT NULL,
                    old_tier VARCHAR,
                    new_tier VARCHAR,
                    seen BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


def _seed(session: Session) -> None:
    session.execute(
        text(
            """
            INSERT INTO mcp_server_registry (server_id, name, risk_tier)
            VALUES (:server_id, :name, :risk_tier)
            """
        ),
        {
            "server_id": _SERVER_ID,
            "name": "Example MCP Server",
            "risk_tier": "critical",
        },
    )
    session.execute(
        text(
            """
            INSERT INTO perspectives (
                id, name, description, facet_filters, created_by, created_at, updated_at
            )
            VALUES (
                :id, :name, :description, :facet_filters, :created_by, :created_at, :updated_at
            )
            """
        ),
        {
            "id": _PERSPECTIVE_ID,
            "name": "Unseen Event Queue Perspective",
            "description": "Acceptance-test perspective",
            "facet_filters": "{}",
            "created_by": "analyst-1",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        },
    )
    session.execute(
        text(
            """
            INSERT INTO perspective_events (
                id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at
            )
            VALUES (
                :id, :perspective_id, :server_id, :change_type, :old_tier, :new_tier, :seen, :created_at
            )
            """
        ),
        [
            {
                "id": 1,
                "perspective_id": _PERSPECTIVE_ID,
                "server_id": _SERVER_ID,
                "change_type": "tier_raise",
                "old_tier": "medium",
                "new_tier": "high",
                "seen": False,
                "created_at": "2024-01-03T00:00:00",
            },
            {
                "id": 2,
                "perspective_id": _PERSPECTIVE_ID,
                "server_id": _SERVER_ID,
                "change_type": "tier_drop",
                "old_tier": "high",
                "new_tier": "medium",
                "seen": False,
                "created_at": "2024-01-02T00:00:00",
            },
            {
                "id": 3,
                "perspective_id": _PERSPECTIVE_ID,
                "server_id": _SERVER_ID,
                "change_type": "tier_raise",
                "old_tier": "low",
                "new_tier": "medium",
                "seen": True,
                "created_at": "2024-01-01T00:00:00",
            },
        ],
    )
    session.commit()


def run_contract() -> None:
    assert Perspective.__tablename__ == "perspectives"
    assert PerspectiveEvent.__tablename__ == "perspective_events"
    assert McpServerRegistry.__tablename__ == "mcp_server_registry"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        _create_test_schema(engine)
        with test_session_factory() as session:
            _seed(session)

        def override_get_session() -> Iterator[Session]:
            with test_session_factory() as session:
                yield session

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get(
                "/api/perspectives/unseen-events",
                params={"limit": 10, "perspective_id": _PERSPECTIVE_ID},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["perspective_id"] == _PERSPECTIVE_ID
        assert payload["limit"] == 10
        assert payload["total"] == 2

        events = payload["events"]
        event_ids = {event["id"] for event in events}
        assert event_ids == {1, 2}
        assert 3 not in event_ids
        assert payload["by_change_type"] == {"tier_raise": 1, "tier_drop": 1}

        for event in events:
            assert event["perspective_id"] == _PERSPECTIVE_ID
            assert event["server_id"] == _SERVER_ID
            assert event["server_name"] == "Example MCP Server"
            assert event["current_risk_tier"] == "critical"
            assert event["seen"] is False
    finally:
        engine.dispose()

    print("PASS")


def run() -> None:
    run_contract()


if __name__ == "__main__":
    run()