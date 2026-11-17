"""Acceptance self-test for the registry assessment staleness service."""
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
            server_id="srv-stale",
            name="Stale Server",
            risk_tier="HIGH",
            trust_score=0.5,
            verdict="approved",
            last_assessed=now - timedelta(days=30),
            last_seen=now - timedelta(days=30),
            first_seen=now - timedelta(days=30),
            scan_count=1,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv-never-assessed",
            name="Never Assessed Server",
            risk_tier="LOW",
            trust_score=0.5,
            verdict="pending",
            last_assessed=None,
            last_seen=now,
            first_seen=now,
            scan_count=0,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv-recent",
            name="Recently Assessed Server",
            risk_tier="MEDIUM",
            trust_score=0.5,
            verdict="approved",
            last_assessed=now - timedelta(hours=1),
            last_seen=now,
            first_seen=now - timedelta(days=1),
            scan_count=1,
        )
    )
    session.commit()
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
    response = client.get(
        "/api/registry/assessment-staleness",
        params={"bucket_hours": 24},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["overall"]["count"] == 3, body
    assert body["overall"]["never_assessed"] == 1, body
    assert body["stalest"], body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)