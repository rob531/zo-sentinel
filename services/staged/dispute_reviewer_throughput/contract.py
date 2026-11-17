"""Acceptance self-test for the dispute reviewer throughput service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    created = datetime(2025, 1, 1, 12, 0, 0)
    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(server_id="srv-1", name="Server 1", risk_tier="HIGH"),
            McpServerRegistry(server_id="srv-2", name="Server 2", risk_tier="HIGH"),
            McpServerRegistry(server_id="srv-3", name="Server 3", risk_tier="LOW"),
            McpServerRegistry(server_id="srv-4", name="Server 4", risk_tier="LOW"),
        ]
    )
    session.add_all(
        [
            McpScoreDispute(
                server_id="srv-1",
                submitted_by="reviewer-a",
                proposed_overall_risk="HIGH",
                reason_category="risk",
                explanation="First resolved dispute",
                status="resolved",
                created_at=created,
                resolved_at=created + timedelta(hours=2),
            ),
            McpScoreDispute(
                server_id="srv-2",
                submitted_by="reviewer-a",
                proposed_overall_risk="HIGH",
                reason_category="risk",
                explanation="Second resolved dispute",
                status="resolved",
                created_at=created + timedelta(hours=1),
                resolved_at=created + timedelta(hours=5),
            ),
            McpScoreDispute(
                server_id="srv-3",
                submitted_by="reviewer-b",
                proposed_overall_risk="LOW",
                reason_category="risk",
                explanation="Open dispute",
                status="open",
                created_at=created,
            ),
            McpScoreDispute(
                server_id="srv-4",
                submitted_by="reviewer-b",
                proposed_overall_risk="LOW",
                reason_category="risk",
                explanation="Rejected dispute",
                status="rejected",
                created_at=created,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/disputes/reviewer-throughput")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["reviewers"]) == 2, body

    reviewer_a = next(
        reviewer for reviewer in body["reviewers"] if reviewer["submitted_by"] == "reviewer-a"
    )
    assert reviewer_a["submitted"] == 2, body
    assert reviewer_a["resolved"] == 2, body
    assert reviewer_a["open"] == 0, body
    assert reviewer_a["median_resolution_hours"] == 3.0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)