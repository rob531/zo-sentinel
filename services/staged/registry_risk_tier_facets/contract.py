"""Acceptance self-test for the registry risk-tier facets service."""

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
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="registry-1",
                name="High Risk Server",
                registry_source="internal",
                risk_tier="HIGH_RISK_ISOLATED",
            ),
            McpServerRegistry(
                server_id="registry-2",
                name="Enterprise Server",
                registry_source="internal",
                risk_tier="ENTERPRISE_CONTROLLED",
            ),
            McpServerRegistry(
                server_id="registry-3",
                name="Trusted Server",
                registry_source="community",
                risk_tier="TRUSTED_GENERAL",
            ),
            McpServerRegistry(
                server_id="registry-4",
                name="Un tiered Server",
                registry_source="community",
                risk_tier=None,
            ),
            McpServerRegistry(
                server_id="registry-5",
                name="Second Trusted Server",
                registry_source="community",
                risk_tier="TRUSTED_GENERAL",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/registry/risk-tier-facets")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 5, body
    assert body["by_risk_tier"]["HIGH_RISK_ISOLATED"] == 1, body
    assert body["untiered"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)