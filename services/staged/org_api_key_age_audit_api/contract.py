"""Acceptance self-test for the organization API key age audit service."""
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
    from app.models import ApiKey, Base, Org

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = TestingSession()
    try:
        session.add(Org(id=1, name="Example Org"))
        session.add_all(
            [
                ApiKey(
                    id=1,
                    org_id=1,
                    key_hash="stale-key-hash-0001",
                    label="old key",
                    created_at=now - timedelta(days=200),
                ),
                ApiKey(
                    id=2,
                    org_id=1,
                    key_hash="recent-key-hash-0002",
                    label="recent key",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id=3,
                    org_id=1,
                    key_hash="current-key-hash-0003",
                    label="current key",
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        session.commit()
    finally:
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
    response = client.get("/api/orgs/1/api-keys/audit")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["org_id"] == 1, body
    assert body["total_keys"] == 3, body
    assert body["stale_keys"] == 1, body
    assert body["stale_threshold_days"] == 90, body

    keys = body["keys"]
    assert len(keys) == 3, body
    stale_keys = [key for key in keys if key["stale"]]
    assert len(stale_keys) == 1, body
    assert stale_keys[0]["age_days"] > 90, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)