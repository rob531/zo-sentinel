"""Acceptance self-test for the staged watchlist tier service."""
from __future__ import annotations

import sys
from datetime import datetime


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-alpha",
                name="Alpha Watch",
                url="https://alpha.example",
                risk_tier="HIGH",
                trust_score=0.91,
                verdict="BLOCK",
                confidence=0.95,
                last_assessed=datetime(2025, 1, 1, 12, 0, 0),
                registry_source="registry",
            ),
            McpServerRegistry(
                server_id="srv-bravo",
                name="Bravo Watch",
                url="https://bravo.example",
                risk_tier="MEDIUM",
                trust_score=0.72,
                verdict="REVIEW",
                confidence=0.88,
                last_assessed=datetime(2025, 1, 3, 12, 0, 0),
                registry_source="registry",
            ),
            McpServerRegistry(
                server_id="srv-charlie",
                name="Charlie Watch",
                url="https://charlie.example",
                risk_tier="LOW",
                trust_score=0.25,
                verdict="ALLOW",
                confidence=0.81,
                last_assessed=datetime(2025, 1, 2, 12, 0, 0),
                registry_source="registry",
            ),
            McpServerRegistry(
                server_id="srv-delta",
                name="Delta Watch",
                url="https://delta.example",
                risk_tier="HIGH",
                trust_score=0.84,
                verdict="BLOCK",
                confidence=0.90,
                last_assessed=None,
                registry_source="registry",
            ),
            McpServerRegistry(
                server_id="srv-echo",
                name="Echo Watch",
                url="https://echo.example",
                risk_tier="MEDIUM",
                trust_score=0.66,
                verdict="REVIEW",
                confidence=0.86,
                last_assessed=datetime(2025, 1, 3, 12, 0, 0),
                registry_source="registry",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)

    response = client.get("/api/watchlist/tiers")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5, body
    assert body["tier"] is None, body
    assert [server["name"] for server in body["servers"]] == [
        "Bravo Watch",
        "Echo Watch",
        "Charlie Watch",
        "Alpha Watch",
        "Delta Watch",
    ], body

    response = client.get("/api/watchlist/tiers", params={"tier": "HIGH"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2, body
    assert body["tier"] == "HIGH", body
    assert {server["server_id"] for server in body["servers"]} == {
        "srv-alpha",
        "srv-delta",
    }, body

    response = client.get(
        "/api/watchlist/tiers", params={"tier": "HIGH", "limit": 1}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2, body
    assert len(body["servers"]) == 1, body

    response = client.get("/api/watchlist/tiers", params={"q": "ALPHA"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1, body
    assert body["servers"][0]["server_id"] == "srv-alpha", body

    response = client.get(
        "/api/watchlist/tiers", params={"limit": 1, "offset": 1}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5, body
    assert body["servers"][0]["name"] == "Echo Watch", body

    response = client.get("/api/watchlist/tiers", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["servers"]) <= 200, body

    response = client.get("/api/watchlist/tiers", params={"limit": -1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["servers"]) == 1, body

    response = client.get(
        "/api/watchlist/tiers", params={"tier": "UNKNOWN_TIER"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0, body
    assert body["servers"] == [], body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)