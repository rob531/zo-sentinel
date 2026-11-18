"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
overrides the real DB session, calls the endpoint and validates the
response. Prints PASS on success, otherwise FAIL and exits non‑zero.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    """Boot the service with a test DB, hit the endpoint and assert expectations."""
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLite in‑memory engine for hermetic testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real app data layer (must be used, not a stub)
    from app.db import get_session
    from app.models import Base, McpServerRegistry

    # Router that implements the endpoint
    from .router import router

    # ------------------------------------------------------------------ #
    # Set up an in‑memory SQLite DB that mirrors the real schema
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed the test data (4 rows covering the required cases)
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sess = TestingSession()
    sess.add(
        McpServerRegistry(
            server_id="srv1",
            name="NeverAssessed",
            risk_tier="MEDIUM",
            registry_source="test",
            last_seen=now,
            last_assessed=None,
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv2",
            name="Fresh",
            risk_tier="MEDIUM",
            registry_source="test",
            last_seen=now,
            last_assessed=now - timedelta(hours=12),  # ≤24h gap
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv3",
            name="Stale5d",
            risk_tier="MEDIUM",
            registry_source="test",
            last_seen=now,
            last_assessed=now - timedelta(days=5),  # 5‑day gap
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv4",
            name="Stale40d",
            risk_tier="MEDIUM",
            registry_source="test",
            last_seen=now,
            last_assessed=now - timedelta(days=40),  # 40‑day gap (worst)
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override so the service uses the in‑memory session
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Build the FastAPI app, mount the router, apply the override
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Exercise the endpoint via TestClient and assert the contract
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/registry/staleness-gap")
    assert response.status_code == 200, response.text
    body = response.json()

    # total rows seeded
    assert body.get("total") == 4, body
    # exactly one never‑assessed entry
    assert body.get("never_assessed") == 1, body
    # worst offender should be the 40‑day stale row
    worst = body.get("worst_offenders", [])
    assert worst, "worst_offenders list is empty"
    assert worst[0].get("server_id") == "srv4", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)