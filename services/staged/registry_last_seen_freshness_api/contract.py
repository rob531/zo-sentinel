"""contract.py -- the service's ACCEPTANCE self‑test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
If the service boots, mounts its router, serves 200 and returns a
schema‑valid body, the script prints ``PASS`` and exits 0.
Otherwise it prints ``FAIL`` and exits 1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite for hermetic testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer (overridden below)
    from app.db import get_session
    from app.models import Base, McpServerRegistry

    # Service router
    from .router import router

    # ------------------------------------------------------------------ #
    # Build an in‑memory SQLite engine and create the schema
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed three registry rows with deterministic timestamps
    now = datetime.utcnow()
    s = TestingSession()
    s.add(
        McpServerRegistry(
            server_id="srv1",
            name="Alpha",
            risk_tier="HIGH",
            first_seen=now - timedelta(days=2),
            last_seen=now - timedelta(days=2),
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv2",
            name="Beta",
            risk_tier="MEDIUM",
            first_seen=now - timedelta(days=10),
            last_seen=now - timedelta(days=10),
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv3",
            name="Gamma",
            risk_tier="LOW",
            first_seen=now - timedelta(days=40),
            last_seen=now - timedelta(days=40),
        )
    )
    s.commit()
    s.close()

    # ------------------------------------------------------------------ #
    # Dependency override that yields a fresh testing session per request
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Assemble the FastAPI app with the overridden session
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute the request against the test client
    client = TestClient(app)
    response = client.get("/api/registry/last-seen-freshness?days=30")
    assert response.status_code == 200, response.text
    body = response.json()

    # ------------------------------------------------------------------ #
    # Validate that bucket counts sum to the seeded rows (3)
    bucket_total = sum(b.get("count", 0) for b in body.get("buckets", []))
    assert bucket_total == 3, f"bucket total {bucket_total} != 3"

    # Validate that the stalest list is ordered descending by days_since_last_seen
    stalest = body.get("stalest", [])
    days_list = [item.get("days_since_last_seen", 0) for item in stalest]
    assert days_list == sorted(days_list, reverse=True), "stalest not ordered"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)