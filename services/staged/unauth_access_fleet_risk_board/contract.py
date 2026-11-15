"""Acceptance self-test for the unauthenticated-access fleet risk board service."""
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
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-low",
                    name="Low-risk server",
                    risk_tier="LOW",
                    trust_score=20,
                    url="https://low.example.test",
                ),
                McpServerRegistry(
                    server_id="srv-medium",
                    name="Medium-risk server",
                    risk_tier="MEDIUM",
                    trust_score=50,
                    url="https://medium.example.test",
                ),
                McpServerRegistry(
                    server_id="srv-high",
                    name="High-risk server",
                    risk_tier="HIGH",
                    trust_score=90,
                    url="https://high.example.test",
                ),
            ]
        )
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="srv-low",
                    unauth_access="full",
                    source="contract",
                    url="https://low.example.test",
                ),
                McpUnauthAccess(
                    server_id="srv-medium",
                    unauth_access="full",
                    source="contract",
                    url="https://medium.example.test",
                ),
                McpUnauthAccess(
                    server_id="srv-high",
                    unauth_access="full",
                    source="contract",
                    url="https://high.example.test",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/unauth/fleet-risk")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["total_servers"] == 3, body
    assert body["unauth_servers"] == 3, body
    assert sum(item["count"] for item in body["matrix"]) == 3, body
    assert all(
        entry.get("server_id") and entry.get("risk_tier")
        for entry in body["top_servers"]
    ), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)