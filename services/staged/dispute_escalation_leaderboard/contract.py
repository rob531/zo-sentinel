"""Acceptance self-test for the dispute escalation leaderboard service."""
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
    from app.models import Base, McpScoreDispute, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(server_id="srv-1", name="Alpha", risk_tier="HIGH"),
                McpServerRegistry(server_id="srv-2", name="Beta", risk_tier="MEDIUM"),
                McpServerRegistry(server_id="srv-3", name="Gamma", risk_tier="LOW"),
            ]
        )
        session.add_all(
            [
                McpScoreDispute(
                    server_id="srv-1",
                    status="open",
                    reason_category="access",
                    proposed_overall_risk=0.8,
                    created_at=now - timedelta(hours=3),
                    resolved_at=None,
                    submitted_by="user-1",
                    explanation="Open access dispute",
                ),
                McpScoreDispute(
                    server_id="srv-1",
                    status="pending",
                    reason_category="quality",
                    proposed_overall_risk=0.7,
                    created_at=now - timedelta(hours=5),
                    resolved_at=None,
                    submitted_by="user-2",
                    explanation="Pending quality dispute",
                ),
                McpScoreDispute(
                    server_id="srv-1",
                    status="resolved",
                    reason_category="access",
                    proposed_overall_risk=0.6,
                    created_at=now - timedelta(hours=10),
                    resolved_at=now - timedelta(hours=2),
                    submitted_by="user-3",
                    explanation="Resolved access dispute",
                ),
                McpScoreDispute(
                    server_id="srv-2",
                    status="resolved",
                    reason_category="safety",
                    proposed_overall_risk=0.9,
                    created_at=now - timedelta(hours=8),
                    resolved_at=now - timedelta(hours=1),
                    submitted_by="user-4",
                    explanation="Resolved safety dispute",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/disputes/escalation-leaderboard")
    assert response.status_code == 200, response.text

    body = response.json()
    assert isinstance(body, dict), body
    leaders = body.get("leaders")
    assert isinstance(leaders, list), body

    seeded = next((leader for leader in leaders if leader.get("server_id") == "srv-1"), None)
    assert seeded is not None, body
    assert seeded.get("open") == 2, seeded
    assert seeded.get("resolved") == 1, seeded
    assert seeded.get("median_resolution_hours") is None or isinstance(
        seeded.get("median_resolution_hours"), float
    ), seeded
    assert seeded.get("top_reason_category") == "access", seeded
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)