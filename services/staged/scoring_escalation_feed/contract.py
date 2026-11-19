"""contract.py -- the service's ACCEPTANCE self‑test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
overrides the real DB dependency, seeds minimal data, hits the endpoint
and validates the response.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data‑layer imports – never stubbed
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpLlmAxisScore

    # Import the router that defines the endpoint under test
    from .router import router

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite engine and create the tables defined in
    # app.models (Base).  This mirrors the production schema but is fully
    # isolated for the self‑test.
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------
    # Seed the DB with three servers and three axis‑score rows (two
    # escalated, one not).  All required columns are supplied explicitly.
    # ------------------------------------------------------------------
    sess = TestingSession()
    sess.add_all(
        [
            McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="HIGH"),
            McpServerRegistry(server_id="srv2", name="Beta", risk_tier="MEDIUM"),
            McpServerRegistry(server_id="srv3", name="Gamma", risk_tier="LOW"),
        ]
    )
    now = datetime.utcnow()
    sess.add_all(
        [
            # Escalated row – today
            McpLlmAxisScore(
                id=1,
                server_id="srv1",
                axis_name="cpu",
                model_version="v1",
                escalated=True,
                escalated_to="LEVEL1",
                p_critical=0.92,
                p_danger=0.05,
                p_top=0.03,
                scored_at=now,
            ),
            # Escalated row – yesterday
            McpLlmAxisScore(
                id=2,
                server_id="srv2",
                axis_name="memory",
                model_version="v1",
                escalated=True,
                escalated_to="LEVEL2",
                p_critical=0.85,
                p_danger=0.10,
                p_top=0.05,
                scored_at=now - timedelta(days=1),
            ),
            # Non‑escalated row – today (should be filtered out)
            McpLlmAxisScore(
                id=3,
                server_id="srv3",
                axis_name="disk",
                model_version="v1",
                escalated=False,
                escalated_to=None,
                p_critical=0.40,
                p_danger=0.30,
                p_top=0.30,
                scored_at=now,
            ),
        ]
    )
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------
    # Dependency override: each request gets a fresh session bound to the
    # in‑memory engine.
    # ------------------------------------------------------------------
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Assemble a minimal FastAPI app, mount the router, and inject the
    # overridden session dependency.
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------
    # Execute the request against the test client and validate the payload.
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/scoring/escalations?days=2")
    assert response.status_code == 200, f"Unexpected status: {response.text}"
    body = response.json()

    # Expected top‑level shape
    assert body.get("days") == 2, f"days mismatch: {body}"
    escalations = body.get("escalations")
    assert isinstance(escalations, list), f"escalations not a list: {body}"
    assert len(escalations) == 2, f"expected 2 escalations, got {len(escalations)}"

    # Verify that a known escalated_to value appears in the result set
    escalated_to_vals = {e.get("escalated_to") for e in escalations}
    assert "LEVEL1" in escalated_to_vals, f"LEVEL1 missing: {escalated_to_vals}"
    assert "LEVEL2" in escalated_to_vals, f"LEVEL2 missing: {escalated_to_vals}"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)