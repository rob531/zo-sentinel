"""contract.py -- acceptance self‑test for the `registry_scan_lag_distribution` service.

Running this module as a script starts a FastAPI app with an in‑memory SQLite
database, seeds it with a few `McpServerRegistry` rows, overrides the real
`get_session` dependency, calls the service endpoint and asserts the expected
response shape and values.

The test must exit with status 0 and print ``PASS`` on success.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    # FastAPI and testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # SQLAlchemy utilities for an in‑memory SQLite DB
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real application data layer (must be used, not a stub)
    from app.db import get_session
    from app.models import Base, McpServerRegistry

    # Router that implements the service logic
    from .router import router

    # ------------------------------------------------------------------ #
    # Set up an in‑memory SQLite engine and create all tables defined in
    # the application's declarative Base.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed the database with three representative rows.
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    sess = TestingSession()
    sess.add(
        McpServerRegistry(
            server_id="srv1",
            name="Never Scanned",
            registry_source="sourceA",
            risk_tier="HIGH",
            last_seen=now,
            # last_scanned left as NULL → never scanned
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv2",
            name="Recently Scanned",
            registry_source="sourceA",
            risk_tier="LOW",
            last_seen=now,
            last_scanned=now - timedelta(days=2),
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv3",
            name="Rescanned",
            registry_source="sourceB",
            risk_tier="MEDIUM",
            last_seen=now,
            last_scanned=now - timedelta(days=10),
            scan_count=3,
        )
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override that yields a fresh session bound to the in‑memory
    # engine for each request.
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Build the FastAPI app, include the router, and apply the override.
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute the request against the test client and validate the response.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    response = client.get("/api/registry/scan-lag")
    assert response.status_code == 200, response.text
    body = response.json()

    # Expected aggregates
    assert body.get("never_scanned") == 1, body
    assert body.get("rescanned_total") == 1, body
    assert isinstance(body.get("by_source"), list) and len(body["by_source"]) == 2, body
    # Buckets should sum to the total number of rows (3)
    bucket_total = sum(b.get("count", 0) for b in body.get("buckets", []))
    assert bucket_total == 3, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 – fail loudly
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)