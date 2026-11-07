"""Acceptance self-test for the registry freshness lag API."""
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
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    now = datetime.utcnow()
    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="fresh-1",
                name="Fresh server",
                registry_source="github",
                first_seen=now - timedelta(days=2),
                last_seen=now - timedelta(hours=2),
                last_scanned=now - timedelta(hours=2),
                scan_count=4,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="fresh-2",
                name="Recently scanned server",
                registry_source="github",
                first_seen=now - timedelta(days=5),
                last_seen=now - timedelta(days=1),
                last_scanned=now - timedelta(days=3),
                scan_count=2,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="stale-1",
                name="Stale server",
                registry_source="github",
                first_seen=now - timedelta(days=90),
                last_seen=now - timedelta(days=45),
                last_scanned=now - timedelta(days=60),
                scan_count=1,
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="never-1",
                name="Never scanned server",
                registry_source="github",
                first_seen=now - timedelta(days=10),
                last_seen=now - timedelta(days=1),
                last_scanned=None,
                scan_count=0,
                risk_tier="MEDIUM",
            ),
            McpServerRegistry(
                server_id="stale-2",
                name="Boundary stale server",
                registry_source="github",
                first_seen=now - timedelta(days=40),
                last_seen=now - timedelta(days=31),
                last_scanned=now - timedelta(days=31),
                scan_count=1,
                risk_tier="HIGH",
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
    response = client.get("/api/registry/freshness/lag", params={"days": 30})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 30, body
    assert body["sources"], body
    assert body["sources"][0]["total"] == 5, body
    assert body["sources"][0]["never_scanned"] == 1, body

    buckets = body["sources"][0]["buckets"]
    assert sum(buckets.values()) == 5, body
    assert body.get("generated_at"), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)