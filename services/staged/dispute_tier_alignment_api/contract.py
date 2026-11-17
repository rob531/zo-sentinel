"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service with an in‑memory SQLite DB, seeds test data, calls the
endpoint and asserts the expected response shape and values.
"""
from __future__ import annotations

import sys
from datetime import datetime

def run() -> bool:
    # FastAPI & testing utilities
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real app data layer
    from app.db import get_session
    from app.models import Base, McpScoreDispute, McpServerRegistry

    # Service router
    from .router import router

    # ------------------------------------------------------------------ #
    # Build an in‑memory SQLite DB that mirrors the real schema
    # ------------------------------------------------------------------ #
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed test data
    # ------------------------------------------------------------------ #
    sess = TestingSession()

    # Servers that exist in the registry
    sess.add(
        McpServerRegistry(
            server_id="srv-aligned",
            name="Aligned Server",
            risk_tier="HIGH",
        )
    )
    sess.add(
        McpServerRegistry(
            server_id="srv-changed",
            name="Changed Server",
            risk_tier="LOW",
        )
    )
    # No registry entry for srv-unknown (to test the unknown branch)

    # Dispute 1 – open, aligned (proposed tier matches registry)
    sess.add(
        McpScoreDispute(
            id=1,
            server_id="srv-aligned",
            submitted_by="analyst1",
            proposed_overall_risk="HIGH",
            reason_category="CATEGORY_A",
            explanation="Explanation A",
            status="OPEN",
        )
    )

    # Dispute 2 – resolved, changed (proposed tier differs from registry)
    sess.add(
        McpScoreDispute(
            id=2,
            server_id="srv-changed",
            submitted_by="analyst2",
            proposed_overall_risk="HIGH",  # differs from registry LOW
            reason_category="CATEGORY_B",
            explanation="Explanation B",
            status="RESOLVED",
            resolved_at=datetime.utcnow(),
        )
    )

    # Dispute 3 – open, unknown server (no registry row)
    sess.add(
        McpScoreDispute(
            id=3,
            server_id="srv-unknown",
            submitted_by="analyst3",
            proposed_overall_risk="MEDIUM",
            reason_category="CATEGORY_C",
            explanation="Explanation C",
            status="OPEN",
        )
    )

    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override so the service uses the in‑memory DB
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Build FastAPI app with the router and run the test request
    # ------------------------------------------------------------------ #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/disputes/tier-alignment")
    assert response.status_code == 200, response.text
    body = response.json()

    # ------------------------------------------------------------------ #
    # Assertions matching the contract description
    # ------------------------------------------------------------------ #
    assert body["total_disputes"] == 3, body
    assert body["aligned_count"] == 1, body
    assert body["changed_count"] == 1, body
    assert body["unknown_count"] == 1, body
    # status buckets
    by_status = body.get("by_status", {})
    assert by_status.get("open", 0) == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)