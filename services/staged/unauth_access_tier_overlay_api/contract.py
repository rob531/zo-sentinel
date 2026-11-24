"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds fixture data, calls the endpoint and asserts the expected response.
"""

from __future__ import annotations

import sys
from datetime import datetime

def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

    from .router import router

    # ------------------------------------------------------------------ #
    # In‑memory SQLite engine and session factory
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed fixture data
    # ------------------------------------------------------------------ #
    sess = TestingSession()
    # Registry rows
    sess.add(
        McpServerRegistry(
            server_id="srv_high",
            name="HighRisk",
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="unknown",
            trust_score=0.0,
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv_trusted",
            name="Trusted",
            risk_tier="TRUSTED_GENERAL",
            verdict="unknown",
            trust_score=0.0,
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv_no_unauth",
            name="NoUnauth",
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="unknown",
            trust_score=0.0,
        )
    )
    # Unauthenticated‑access rows (only for first two servers)
    sess.add(
        McpUnauthAccess(
            server_id="srv_high",
            url="http://example.com/high",
            unauth_access="yes",
            source="test",
            updated_at=datetime.utcnow(),
        )
    )
    sess.add(
        McpUnauthAccess(
            server_id="srv_trusted",
            url="http://example.com/trusted",
            unauth_access="no",
            source="test",
            updated_at=datetime.utcnow(),
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override to use the in‑memory session
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Build FastAPI app with router and overrides
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute test request
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/unauth-access/tier-overlay")
    assert response.status_code == 200, response.text
    body = response.json()

    # Expected: two servers (those with unauth rows)
    assert body.get("count") == 2, body
    servers = body.get("servers", [])
    assert len(servers) == 2, body

    # First server should be the HIGH_RISK_ISOLATED one
    first = servers[0]
    assert first.get("server_id") == "srv_high", body
    assert first.get("risk_tier") == "HIGH_RISK_ISOLATED", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)