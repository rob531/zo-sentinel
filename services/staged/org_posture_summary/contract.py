from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import (
        ApiKey,
        Base,
        McpServerRegistry,
        Org,
        Perspective,
        PerspectiveSnapshot,
        User,
    )

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=200)

    session = TestingSession()
    try:
        session.add(Org(id=1, name="acme", created_at=now))
        session.add_all(
            [
                User(
                    id=1,
                    email="admin@acme.test",
                    org_id=1,
                    role="admin",
                    created_at=now,
                    password_hash="test-admin-hash",
                ),
                User(
                    id=2,
                    email="analyst@acme.test",
                    org_id=1,
                    role="analyst",
                    created_at=now,
                    password_hash="test-analyst-hash",
                ),
                ApiKey(
                    id=1,
                    org_id=1,
                    key_hash="stale-api-key-hash",
                    label="stale",
                    created_at=old,
                ),
                ApiKey(
                    id=2,
                    org_id=1,
                    key_hash="recent-api-key-hash",
                    label="recent",
                    created_at=now,
                ),
                Perspective(
                    id=1,
                    org_id=1,
                    name="acme perspective",
                    created_by=1,
                    description="Test perspective",
                    facet_filters={},
                    created_at=now,
                ),
                PerspectiveSnapshot(
                    id=1,
                    perspective_id=1,
                    membership=[
                        {
                            "server_id": "srv-trusted",
                            "name": "Trusted Server",
                        },
                        {
                            "server_id": "srv-high",
                            "name": "High Risk Server",
                        },
                        {
                            "server_id": "srv-unknown",
                            "name": "Unknown Server",
                        },
                    ],
                    taken_at=now,
                ),
                McpServerRegistry(
                    server_id="srv-trusted",
                    name="Trusted Server",
                    risk_tier="trusted",
                    trust_score=0.9,
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="srv-high",
                    name="High Risk Server",
                    risk_tier="high",
                    trust_score=0.4,
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="srv-unknown",
                    name="Unknown Server",
                    risk_tier="unknown",
                    trust_score=None,
                    last_assessed=None,
                ),
            ]
        )
        session.commit()
    finally:
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

    response = client.get("/api/orgs/1/posture")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["org_id"] == 1, body
    assert body["member_count"] == 2, body
    assert body["role_breakdown"] == {"admin": 1, "analyst": 1}, body
    assert body["api_key_count"] == 2, body
    assert body["stale_api_key_count"] == 1, body
    assert body["perspective_count"] == 1, body
    assert body["server_posture"]["high_or_critical_count"] == 1, body
    assert body["server_posture"]["by_tier"]["trusted"] == 1, body

    missing = client.get("/api/orgs/999999/posture")
    assert missing.status_code == 404, missing.text
    assert missing.json() == {"detail": "org not found"}, missing.json()

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)