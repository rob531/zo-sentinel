"""Acceptance self-test for the registry freshness SLA API."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


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

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="fresh-server",
                name="Fresh server",
                registry_source="source-fresh",
                risk_tier="LOW",
                verdict="PASS",
                first_seen=now - timedelta(days=2),
                last_seen=now - timedelta(hours=1),
                last_scanned=now - timedelta(hours=1),
                last_assessed=now - timedelta(days=1),
            ),
            McpServerRegistry(
                server_id="stale-server",
                name="Stale server",
                registry_source="source-stale",
                risk_tier="MEDIUM",
                verdict="PASS",
                first_seen=now - timedelta(days=20),
                last_seen=now - timedelta(days=2),
                last_scanned=now - timedelta(days=2),
                last_assessed=now - timedelta(days=10),
            ),
            McpServerRegistry(
                server_id="breached-server",
                name="Breached server",
                registry_source="source-breached",
                risk_tier="HIGH",
                verdict="PASS",
                first_seen=now - timedelta(days=90),
                last_seen=now - timedelta(days=5),
                last_scanned=now - timedelta(days=5),
                last_assessed=now - timedelta(days=60),
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
    response = client.get("/api/registry/freshness-sla")
    assert response.status_code == 200, response.text

    body = response.json()
    sources = body["sources"]
    assert len(sources) == 3, body

    by_source = {entry["registry_source"]: entry for entry in sources}
    assert "source-fresh" in by_source, body
    assert "source-breached" in by_source, body

    fresh = by_source["source-fresh"]
    breached = by_source["source-breached"]
    assert fresh["total"] == 1, body
    assert fresh["breach_count"] == 0, body
    assert fresh["sla_breach_rate"] == 0, body
    assert breached["total"] == 1, body
    assert breached["breach_count"] == 1, body
    assert breached["sla_breach_rate"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)