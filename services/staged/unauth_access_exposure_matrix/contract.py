"""Acceptance self-test for the unauthenticated access exposure matrix service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="unauth-srv-1",
                name="Public MCP Server",
                risk_tier="HIGH",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="unauth-srv-2",
                name="Authenticated MCP Server",
                risk_tier="HIGH",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="unauth-srv-3",
                name="Internal MCP Server",
                risk_tier="LOW",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="unauth-srv-4",
                name="Protected MCP Server",
                risk_tier="LOW",
                registry_source="test",
            ),
        ]
    )
    session.add_all(
        [
            McpUnauthAccess(
                server_id="unauth-srv-1",
                url="https://public.example/mcp",
                unauth_access="EXPOSED",
                source="scan",
            ),
            McpUnauthAccess(
                server_id="unauth-srv-2",
                url="https://authenticated.example/mcp",
                unauth_access="SAFE",
                source="scan",
            ),
            McpUnauthAccess(
                server_id="unauth-srv-3",
                url="https://internal.example/mcp",
                unauth_access="EXPOSED",
                source="catalog",
            ),
            McpUnauthAccess(
                server_id="unauth-srv-4",
                url="https://protected.example/mcp",
                unauth_access="SAFE",
                source="catalog",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/unauth-access/matrix")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["exposure_states"]) == 2, body
    assert body["totals"]["servers"] == 4, body
    assert body["totals"]["exposed_servers"] == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)