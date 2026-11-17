from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="scheme-https",
                name="HTTPS server",
                url="https://example.test/mcp",
                registry_source="test",
                trust_score=90,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="scheme-http",
                name="HTTP server",
                url="http://example.test/mcp",
                registry_source="test",
                trust_score=50,
                risk_tier="MEDIUM",
            ),
            McpServerRegistry(
                server_id="scheme-wss",
                name="WSS server",
                url="wss://example.test/mcp",
                registry_source="test",
                trust_score=85,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="scheme-ws",
                name="WS server",
                url="ws://example.test/mcp",
                registry_source="test",
                trust_score=40,
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="scheme-none",
                name="Server without URL",
                url=None,
                registry_source="test",
                trust_score=0,
                risk_tier="UNKNOWN",
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
    response = client.get("/api/registry/url-scheme-audit", params={"limit": 100})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 5, body
    assert sum(body["by_scheme"].values()) == 5, body
    assert body["insecure_count"] == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)