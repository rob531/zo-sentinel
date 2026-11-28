"""contract.py -- the service's ACCEPTANCE self‑test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
overrides the real DB dependency, seeds test data, hits the endpoint and
asserts the expected contract.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy core for an in‑memory SQLite DB
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real app data layer (must be used, not stubbed)
    from app.db import get_session
    from app.models import Base, ApiKey, Org

    # Router for this service
    from .router import router

    # ------------------------------------------------------------------ #
    # Build an in‑memory SQLite engine and create the schema
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed test data
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sess = TestingSession()
    # Orgs
    org_a = Org(id="org1", name="Alpha Org")
    org_b = Org(id="org2", name="Beta Org")
    sess.add_all([org_a, org_b])
    # ApiKeys
    sess.add_all(
        [
            ApiKey(
                id="key1",
                org_id="org1",
                key_hash="hash1",
                label="stale-legacy",
                created_at=now - timedelta(days=200),
            ),
            ApiKey(
                id="key2",
                org_id="org1",
                key_hash="hash2",
                label="fresh-key",
                created_at=now - timedelta(days=10),
            ),
            ApiKey(
                id="key3",
                org_id="org2",
                key_hash="hash3",
                label="old-other-org",
                created_at=now - timedelta(days=120),
            ),
        ]
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override that yields a fresh session per request
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Build FastAPI app, mount router, inject override
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Run test client against the endpoint
    # ------------------------------------------------------------------ #
    client = TestClient(app)

    # Unfiltered request
    resp = client.get("/api/orgs/api-key-age")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3, body
    assert body["stale_total"] == 2, body
    # At least one key must be >= 100 days old
    assert any(k["age_days"] >= 100 for k in body["keys"]), body
    # Ensure no key_hash leaked in the response body
    assert "hash1" not in resp.text and "hash2" not in resp.text and "hash3" not in resp.text, resp.text

    # Org‑scoped request
    resp_org = client.get("/api/orgs/api-key-age?org_id=org1")
    assert resp_org.status_code == 200, resp_org.text
    body_org = resp_org.json()
    assert body_org["total"] == 2, body_org
    assert body_org["stale_total"] == 1, body_org

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)