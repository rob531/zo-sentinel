"""Acceptance self-test for the staged registry facet count API."""
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
                server_id="srv1",
                name="Registry A",
                registry_source="smithery",
                risk_tier="HIGH",
                verdict="trusted",
                trust_score=90,
            ),
            McpServerRegistry(
                server_id="srv2",
                name="Registry B",
                registry_source="smithery",
                risk_tier="HIGH",
                verdict="trusted",
                trust_score=80,
            ),
            McpServerRegistry(
                server_id="srv3",
                name="Registry C",
                registry_source="glama",
                risk_tier="LOW",
                verdict="review",
                trust_score=None,
            ),
            McpServerRegistry(
                server_id="srv4",
                name="Registry D",
                registry_source="custom",
                risk_tier="MEDIUM",
                verdict="unknown",
                trust_score=70,
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
    response = client.get("/api/registry/facets")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_servers"] == 4, body
    risk_buckets = body["by_risk_tier"]
    assert sum(1 for bucket in risk_buckets if bucket["count"] == 2) == 1, body
    assert body["unscored"]["missing_trust_score"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)