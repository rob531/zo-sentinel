"""Acceptance self-test for the dispute tier conflict API."""

from __future__ import annotations

import sys


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

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="dispute-server-1",
                name="Equal Tier Server",
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="dispute-server-2",
                name="Conflicting Tier Server",
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="dispute-server-3",
                name="Unknown Tier Server",
                risk_tier=None,
            ),
        ]
    )
    session.commit()

    session.add_all(
        [
            McpScoreDispute(
                server_id="dispute-server-1",
                proposed_overall_risk="HIGH",
                proposed_axes={"confidence": 0.9},
                status="closed",
                reason_category="model_agreement",
                explanation="The proposed tier agrees with the registry tier.",
                submitted_by="analyst",
            ),
            McpScoreDispute(
                server_id="dispute-server-2",
                proposed_overall_risk="LOW",
                proposed_axes={"confidence": 0.4},
                status="open",
                reason_category="tier_disagreement",
                explanation="The proposed tier differs from the registry tier.",
                submitted_by="analyst",
            ),
            McpScoreDispute(
                server_id="dispute-server-3",
                proposed_overall_risk="MEDIUM",
                proposed_axes={"confidence": 0.7},
                status="open",
                reason_category="missing_registry_tier",
                explanation="The registry tier is unavailable.",
                submitted_by="analyst",
            ),
        ]
    )
    session.commit()
    session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    response = client.get("/api/disputes/conflicts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1, body
    assert len(body["conflicts"]) == 1, body

    conflict = body["conflicts"][0]
    assert conflict["dispute_id"] == 2, body
    assert conflict["server_id"] == "dispute-server-2", body
    assert conflict["name"] == "Conflicting Tier Server", body
    assert conflict["current_risk_tier"] == "HIGH", body
    assert conflict["proposed_overall_risk"] == "LOW", body
    assert conflict["status"] == "open", body

    open_response = client.get("/api/disputes/conflicts?status=open")
    assert open_response.status_code == 200, open_response.text
    open_body = open_response.json()
    assert open_body["count"] == 1, open_body
    assert len(open_body["conflicts"]) == 1, open_body
    assert all(row["status"] == "open" for row in open_body["conflicts"]), open_body
    assert open_body["conflicts"][0]["dispute_id"] == 2, open_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)