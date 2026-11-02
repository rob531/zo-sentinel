"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds minimal data, and asserts that the endpoint returns a valid response.
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

    # Real data layer imports (must not be stubbed)
    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpScoreDispute

    # Router for this service
    from .router import router

    # ------------------------------------------------------------------
    # In‑memory SQLite engine and session factory
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------
    # Seed test data
    # ------------------------------------------------------------------
    sess = TestingSession()
    # Servers
    srv_a = McpServerRegistry(
        server_id="srvA",
        name="ServerA",
        risk_tier="HIGH",
        verdict="ACCEPTED",
    )
    srv_b = McpServerRegistry(
        server_id="srvB",
        name="ServerB",
        risk_tier="MEDIUM",
        verdict="REJECTED",
    )
    srv_c = McpServerRegistry(
        server_id="srvC",
        name="ServerC",
        risk_tier="LOW",
        verdict="ACCEPTED",
    )
    sess.add_all([srv_a, srv_b, srv_c])

    now = datetime.utcnow()
    # Disputes
    disputes = [
        McpScoreDispute(
            id=1,
            server_id="srvA",
            submitted_by="analyst1",
            proposed_overall_risk=0.7,
            proposed_axes=None,
            reason_category="axis_disagreement",
            explanation="Test dispute 1",
            status="open",
            created_at=now - timedelta(days=2),
        ),
        McpScoreDispute(
            id=2,
            server_id="srvB",
            submitted_by="analyst2",
            proposed_overall_risk=0.4,
            proposed_axes=None,
            reason_category="policy_violation",
            explanation="Test dispute 2",
            status="open",
            created_at=now - timedelta(days=1),
        ),
        McpScoreDispute(
            id=3,
            server_id="srvA",
            submitted_by="analyst3",
            proposed_overall_risk=0.9,
            proposed_axes=None,
            reason_category="axis_disagreement",
            explanation="Test dispute 3",
            status="resolved",
            created_at=now - timedelta(days=5),
            resolved_at=now - timedelta(days=1, hours=5),
        ),
        McpScoreDispute(
            id=4,
            server_id="srvC",
            submitted_by="analyst4",
            proposed_overall_risk=0.2,
            proposed_axes=None,
            reason_category="other",
            explanation="Test dispute 4",
            status="resolved",
            created_at=now - timedelta(days=4),
            resolved_at=now - timedelta(days=2, hours=3),
        ),
    ]
    sess.add_all(disputes)
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------
    # Dependency override to use the testing session
    # ------------------------------------------------------------------
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Build FastAPI app with router and overrides
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------
    # Execute test request
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/disputes/ledger?status=all&days=30")
    assert response.status_code == 200, response.text
    body = response.json()

    # ------------------------------------------------------------------
    # Assertions on response payload
    # ------------------------------------------------------------------
    assert body["total"] == 4, body
    by_status = body.get("by_status", {})
    assert by_status.get("open") == 2, by_status
    assert by_status.get("resolved") == 2, by_status

    # Ensure at least one dispute references the seeded server name
    dispute_names = {d.get("server_name") for d in body.get("disputes", [])}
    assert "ServerA" in dispute_names, dispute_names

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)