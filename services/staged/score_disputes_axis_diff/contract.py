"""Acceptance self-test for the score dispute axis-diff service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpScoreDispute, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add(
        McpScoreDispute(
            id=1,
            server_id="srv-a",
            proposed_overall_risk="HIGH_RISK_ISOLATED",
            proposed_axes={"auth_strength": "weak"},
            status="open",
            reason_category="test_dispute",
            explanation="Axis proposal differs from current assessment.",
            submitted_by="contract-test",
        )
    )
    session.add(
        McpLlmAxisScore(
            id=1,
            server_id="srv-a",
            axis_name="auth_strength",
            label="strong",
            label_index=0,
            probs={"strong": 0.9},
            p_top=0.9,
            escalated=False,
            decision_rule_version="test",
            model_version="test",
        )
    )
    session.add(
        McpLlmAxisScore(
            id=2,
            server_id="srv-a",
            axis_name="capability_breadth",
            label="broad",
            label_index=0,
            probs={"broad": 0.6},
            p_top=0.6,
            escalated=False,
            decision_rule_version="test",
            model_version="test",
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv-a",
            name="Server A",
            risk_tier="TRUSTED_GENERAL",
            verdict="TRUSTED_GENERAL",
            trust_score=0.9,
        )
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
    response = client.get("/api/disputes/1/axis-diff")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["axes"]) == 2, body

    auth_strength = next(
        axis for axis in body["axes"] if axis["axis_name"] == "auth_strength"
    )
    assert auth_strength["agrees"] is False, body
    assert "capability_breadth" in body["missing_proposals"], body
    assert body["proposed_tier"] == "HIGH_RISK_ISOLATED", body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)