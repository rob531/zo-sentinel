"""Acceptance contract for the axis escalation flow API."""

from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session

try:
    from app.models import McpLlmAxisScore
except ImportError:
    from app.models import McpLlmAxisScore as McpLlmAxisScore


router_module = import_module(
    "services.staged.axis_escalation_flow_api.router"
)
api_router = getattr(router_module, "router")


def _make_test_database() -> tuple[object, sessionmaker]:
    """Create the isolated SQLite database used by this contract."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT,
                    label_index INTEGER,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated BOOLEAN NOT NULL DEFAULT 0,
                    escalated_to TEXT,
                    scored_at DATETIME NOT NULL
                )
                """
            )
        )

    return engine, testing_session


def _seed(session: Session) -> None:
    """Seed three servers, two axes, and two scoring days."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = now - timedelta(days=1)

    session.execute(
        text(
            """
            INSERT INTO mcp_llm_axis_scores (
                server_id,
                axis_name,
                label,
                label_index,
                p_top,
                p_critical,
                p_danger,
                escalated,
                escalated_to,
                scored_at
            ) VALUES (
                :server_id,
                :axis_name,
                :label,
                :label_index,
                :p_top,
                :p_critical,
                :p_danger,
                :escalated,
                :escalated_to,
                :scored_at
            )
            """
        ),
        [
            {
                "server_id": "server-1",
                "axis_name": "availability",
                "label": "critical",
                "label_index": 3,
                "p_top": 0.1,
                "p_critical": 0.7,
                "p_danger": 0.2,
                "escalated": True,
                "escalated_to": "security",
                "scored_at": now,
            },
            {
                "server_id": "server-2",
                "axis_name": "availability",
                "label": "danger",
                "label_index": 2,
                "p_top": 0.2,
                "p_critical": 0.4,
                "p_danger": 0.4,
                "escalated": True,
                "escalated_to": None,
                "scored_at": yesterday,
            },
            {
                "server_id": "server-3",
                "axis_name": "capacity",
                "label": "critical",
                "label_index": 3,
                "p_top": 0.05,
                "p_critical": 0.8,
                "p_danger": 0.15,
                "escalated": True,
                "escalated_to": None,
                "scored_at": now,
            },
        ],
    )
    session.commit()


def _build_app(session_factory: sessionmaker) -> FastAPI:
    application = FastAPI()

    route_paths = [
        getattr(route, "path", "")
        for route in getattr(api_router, "routes", [])
    ]
    router_has_api_prefix = any(path.startswith("/api/") for path in route_paths)

    if router_has_api_prefix:
        application.include_router(api_router)
    else:
        application.include_router(api_router, prefix="/api")

    def override_get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_session] = override_get_session
    return application


def main() -> None:
    engine, session_factory = _make_test_database()
    try:
        session = session_factory()
        try:
            _seed(session)
        finally:
            session.close()

        application = _build_app(session_factory)
        with TestClient(application) as client:
            response = client.get("/api/axis/escalation-flow", params={"days": 30})

        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["days"] == 30
        assert payload["total_escalated"] == 3

        axes = {axis["axis_name"]: axis for axis in payload["axes"]}
        assert "availability" in axes
        assert "capacity" in axes
        assert axes["availability"]["escalated"] == 2
        assert axes["capacity"]["escalated"] == 1
        assert axes["availability"]["targets"]["security"] == 1
        assert axes["availability"]["targets"]["self"] == 1
        assert axes["capacity"]["targets"]["self"] == 1

        print("PASS")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()