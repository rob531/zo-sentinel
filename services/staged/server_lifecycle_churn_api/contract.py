"""Acceptance self-test for the server lifecycle churn API."""
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
    recent = now - timedelta(days=2)
    old = now - timedelta(days=60)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="lifecycle-new",
                name="New server",
                registry_source="test",
                first_seen=recent,
                last_seen=recent,
                last_scanned=recent,
                scan_count=1,
                risk_tier="LOW",
                trust_score=90.0,
            ),
            McpServerRegistry(
                server_id="lifecycle-active",
                name="Active server",
                registry_source="test",
                first_seen=old,
                last_seen=recent,
                last_scanned=recent,
                scan_count=2,
                risk_tier="MEDIUM",
                trust_score=75.0,
            ),
            McpServerRegistry(
                server_id="lifecycle-dormant",
                name="Dormant server",
                registry_source="test",
                first_seen=old,
                last_seen=old,
                last_scanned=old,
                scan_count=1,
                risk_tier="HIGH",
                trust_score=40.0,
            ),
            McpServerRegistry(
                server_id="lifecycle-never-scanned",
                name="Never scanned server",
                registry_source="test",
                first_seen=old,
                last_seen=old,
                scan_count=0,
                risk_tier="UNKNOWN",
                trust_score=0.0,
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

    with TestClient(app) as client:
        response = client.get("/api/registry/lifecycle/churn?days=30")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["days"] == 30, body
    assert body["total"] == 4, body
    assert len(body["cohorts"]) == 4, body

    counts = {entry["cohort"]: entry["count"] for entry in body["cohorts"]}
    assert counts["NEW"] == 1, body
    assert counts["ACTIVE"] == 1, body
    assert counts["DORMANT"] == 1, body
    assert counts["NEVER_SCANNED"] == 1, body
    assert sum(counts.values()) == 4, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)