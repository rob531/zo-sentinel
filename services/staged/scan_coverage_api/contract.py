"""Acceptance self-test for the scan coverage API."""
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
    TestingSession = sessionmaker(bind=engine, autoflush=False)

    now = datetime.utcnow()
    session = TestingSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    scan_count=None,
                    last_scanned=None,
                    last_seen=now,
                    last_assessed=None,
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="scanned-2h",
                    name="Scanned two hours ago",
                    scan_count=1,
                    last_scanned=now - timedelta(hours=2),
                    last_seen=now,
                    last_assessed=now,
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="scanned-5d",
                    name="Scanned five days ago",
                    scan_count=1,
                    last_scanned=now - timedelta(days=5),
                    last_seen=now,
                    last_assessed=now,
                    risk_tier="LOW",
                ),
                McpServerRegistry(
                    server_id="scanned-40d",
                    name="Scanned forty days ago",
                    scan_count=1,
                    last_scanned=now - timedelta(days=40),
                    last_seen=now,
                    last_assessed=now,
                    risk_tier="CRITICAL",
                ),
                McpServerRegistry(
                    server_id="zero-count-recently-seen",
                    name="Zero scans but recently seen",
                    scan_count=0,
                    last_scanned=None,
                    last_seen=now,
                    last_assessed=None,
                    risk_tier="HIGH",
                ),
            ]
        )
        session.commit()
    finally:
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
    response = client.get("/api/servers/scan-coverage")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5, body
    assert body["buckets"]["never_scanned"] == 2, body
    assert body["buckets"]["stale_over_30d"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)