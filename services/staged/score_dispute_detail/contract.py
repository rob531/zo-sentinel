"""Acceptance self-test for the score_dispute_detail service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpScoreDispute

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
            server_id="srv-score-1",
            submitted_by="reviewer@example.test",
            proposed_overall_risk="HIGH_RISK_ISOLATED",
            proposed_axes=["overall_risk", "auth_strength"],
            reason_category="model_disagreement",
            explanation="The disputed score should be reviewed.",
            status="pending",
            admin_note=None,
        )
    )
    session.add(
        McpLlmAxisScore(
            id=1,
            server_id="srv-score-1",
            axis_name="overall_risk",
            label="high",
            label_index=3,
            probs={"high": 0.91},
            p_top=0.91,
            p_critical=0.02,
            p_danger=0.04,
            escalated=False,
            escalated_to=None,
            decision_rule_version="rules-v1",
            model_version="model-v1",
        )
    )
    session.add(
        McpLlmAxisScore(
            id=2,
            server_id="srv-score-1",
            axis_name="auth_strength",
            label="weak",
            label_index=1,
            probs={"weak": 0.88},
            p_top=0.88,
            p_critical=0.01,
            p_danger=0.03,
            escalated=True,
            escalated_to="security-review",
            decision_rule_version="rules-v1",
            model_version="model-v1",
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
    route_paths = [getattr(route, "path", "") for route in router.routes]
    if any(path.startswith("/api/disputes/") for path in route_paths):
        app.include_router(router)
    else:
        app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/disputes/1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dispute"]["id"] == 1, body
    assert body["dispute"]["proposed_overall_risk"] == "HIGH_RISK_ISOLATED", body
    assert len(body["current_axes"]) == 2, body
    assert any(axis["axis_name"] == "overall_risk" for axis in body["current_axes"]), body

    missing = client.get("/api/disputes/999999")
    assert missing.status_code == 404, missing.text
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)