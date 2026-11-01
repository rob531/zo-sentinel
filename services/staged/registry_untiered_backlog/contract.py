"""Acceptance self-test for the registry untiered backlog service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta
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

    now = datetime.utcnow()
    session = TestingSession()
    session.add(
        McpServerRegistry(
            server_id="srv-tiered",
            name="Tiered server",
            registry_source="github",
            risk_tier="CAUTION_LIMITED",
            last_assessed=now - timedelta(days=1),
            last_seen=now,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv-old",
            name="Old untiered server",
            registry_source="npm",
            risk_tier=None,
            last_assessed=now - timedelta(days=90),
            last_seen=now,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv-recent",
            name="Recent untiered server",
            registry_source="npm",
            risk_tier="",
            last_assessed=now - timedelta(days=1),
            last_seen=now,
        )
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
    response = client.get("/api/registry/untiered-backlog?days=30")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 30, body
    assert body["total_untiered"] == 2, body
    assert sum(item["count"] for item in body["by_source"]) == 2, body
    assert body["stale_unscored"], body
    assert body["stale_unscored"][0]["server_id"] == "srv-old", body
    assert body["oldest_age_days"] >= 90, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)