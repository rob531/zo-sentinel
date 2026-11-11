"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
overrides the real DB dependency, seeds test data, hits the endpoint and
asserts the expected response.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

def run() -> bool:
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer (will be overridden)
    from app.db import get_session
    from app.models import Base, FreshnessStat

    # Service router
    from .router import router

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite DB that mimics the real schema
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed two rows: one fresh, one stale (> 1 h old)
    now = datetime.now(timezone.utc)
    fresh = FreshnessStat(
        surface="fresh_surface",
        payload={"msg": "fresh"},
        compute_seconds=5,
        computed_at=now,
    )
    stale = FreshnessStat(
        surface="stale_surface",
        payload={"msg": "stale"},
        compute_seconds=7,
        computed_at=now - timedelta(hours=2),
    )
    sess = TestingSession()
    sess.add_all([fresh, stale])
    sess.commit()
    sess.close()

    # Dependency override that yields a fresh session per request
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Build FastAPI app with the router and the overridden DB session
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------
    # Execute the request and validate the contract
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/freshness/surfaces")
    assert response.status_code == 200, response.text
    body = response.json()

    # Basic shape checks
    assert "generated_at" in body, body
    assert isinstance(body.get("surfaces"), list), body
    assert isinstance(body.get("stale_count"), int), body

    surfaces = body["surfaces"]
    assert len(surfaces) == 2, body
    assert body["stale_count"] == 1, body

    # Find the fresh surface entry and ensure it is not marked stale
    fresh_entry = next(s for s in surfaces if s["surface"] == "fresh_surface")
    assert fresh_entry.get("stale") is False, fresh_entry

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)