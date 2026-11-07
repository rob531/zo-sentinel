"""Acceptance self-test for the registry risk-tier matrix API."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="risk-tier-matrix-1",
                name="Matrix Server 1",
                registry_source="registry_a",
                risk_tier="CRITICAL",
            ),
            McpServerRegistry(
                server_id="risk-tier-matrix-2",
                name="Matrix Server 2",
                registry_source="registry_a",
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="risk-tier-matrix-3",
                name="Matrix Server 3",
                registry_source="registry_a",
                risk_tier=None,
            ),
            McpServerRegistry(
                server_id="risk-tier-matrix-4",
                name="Matrix Server 4",
                registry_source="registry_b",
                risk_tier="MEDIUM",
            ),
            McpServerRegistry(
                server_id="risk-tier-matrix-5",
                name="Matrix Server 5",
                registry_source="registry_b",
                risk_tier=None,
            ),
        ]
    )
    session.commit()
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

    client = TestClient(app)
    response = client.get("/api/registry/risk-tier-matrix")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_servers"] == 5, body
    assert body["tier_totals"]["UNSCORED"] == 2, body
    assert body["tier_totals"]["CRITICAL"] == 1, body
    assert body["tier_totals"]["HIGH"] == 1, body
    assert body["tier_totals"]["MEDIUM"] == 1, body

    sources = {row["registry_source"]: row for row in body["by_source"]}
    assert sources["registry_a"]["total"] == 3, body
    assert sources["registry_a"]["tiers"]["UNSCORED"] == 1, body
    assert sources["registry_a"]["tiers"]["HIGH"] == 1, body
    assert sources["registry_b"]["total"] == 2, body
    assert sources["registry_b"]["tiers"]["UNSCORED"] == 1, body
    assert sources["registry_b"]["tiers"]["MEDIUM"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)