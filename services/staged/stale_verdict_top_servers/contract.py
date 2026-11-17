"""Acceptance self-test for the stale-verdict registry service."""
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
    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="stale-never-assessed",
                name="Never assessed",
                verdict="unknown",
                risk_tier="HIGH",
                confidence=0.0,
                last_assessed=None,
                last_seen=now - timedelta(hours=200),
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="stale-fresh",
                name="Fresh assessment",
                verdict="safe",
                risk_tier="LOW",
                confidence=0.95,
                last_assessed=now - timedelta(hours=1),
                last_seen=now,
                scan_count=3,
            ),
            McpServerRegistry(
                server_id="stale-old-1",
                name="Old assessment one",
                verdict="suspect",
                risk_tier="MEDIUM",
                confidence=0.7,
                last_assessed=now - timedelta(hours=72),
                last_seen=now - timedelta(hours=72),
                scan_count=2,
            ),
            McpServerRegistry(
                server_id="stale-old-2",
                name="Old assessment two",
                verdict="unsafe",
                risk_tier="HIGH",
                confidence=0.85,
                last_assessed=now - timedelta(hours=100),
                last_seen=now - timedelta(hours=100),
                scan_count=1,
            ),
        ]
    )
    session.commit()
    session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/registry/stale-verdicts", params={"hours": 48, "limit": 50})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_stale"] == 3, body
    assert body["never_assessed_count"] == 1, body
    assert len(body["stale_servers"]) <= 50, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)