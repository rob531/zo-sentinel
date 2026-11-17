"""Acceptance self-test for the axis escalation feed service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-alpha",
                    name="Alpha Server",
                    risk_tier="HIGH",
                    verdict="ESCALATED",
                    trust_score=0.91,
                ),
                McpServerRegistry(
                    server_id="srv-beta",
                    name="Beta Server",
                    risk_tier="MEDIUM",
                    verdict="REVIEW",
                    trust_score=0.74,
                ),
                McpServerRegistry(
                    server_id="srv-gamma",
                    name="Gamma Server",
                    risk_tier="LOW",
                    verdict="TRUSTED",
                    trust_score=0.96,
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=101,
                    server_id="srv-alpha",
                    axis_name="security",
                    label="critical",
                    label_index=3,
                    p_top=0.91,
                    p_critical=0.88,
                    p_danger=0.74,
                    escalated=True,
                    escalated_to="security-review",
                    decision_rule_version="rules-2025-01",
                    model_version="axis-model-1",
                    scored_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=102,
                    server_id="srv-alpha",
                    axis_name="reliability",
                    label="degraded",
                    label_index=2,
                    p_top=0.84,
                    p_critical=0.61,
                    p_danger=0.79,
                    escalated=True,
                    escalated_to="reliability-review",
                    decision_rule_version="rules-2025-01",
                    model_version="axis-model-1",
                    scored_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=103,
                    server_id="srv-beta",
                    axis_name="security",
                    label="moderate",
                    label_index=1,
                    p_top=0.62,
                    p_critical=0.43,
                    p_danger=0.55,
                    escalated=False,
                    decision_rule_version="rules-2025-01",
                    model_version="axis-model-1",
                    scored_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=104,
                    server_id="srv-gamma",
                    axis_name="security",
                    label="nominal",
                    label_index=0,
                    p_top=0.12,
                    p_critical=0.08,
                    p_danger=0.10,
                    escalated=False,
                    decision_rule_version="rules-2025-01",
                    model_version="axis-model-1",
                    scored_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=105,
                    server_id="srv-gamma",
                    axis_name="privacy",
                    label="nominal",
                    label_index=0,
                    p_top=0.09,
                    p_critical=0.04,
                    p_danger=0.06,
                    escalated=False,
                    decision_rule_version="rules-2025-01",
                    model_version="axis-model-1",
                    scored_at=datetime(2024, 12, 31, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()
    finally:
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

    response = client.get("/api/axis/escalations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2, body
    assert sum(body["axis_counts"].values()) == 2, body
    assert body["axis_counts"].get("security") == 1, body
    assert body["axis_counts"].get("reliability") == 1, body

    response = client.get("/api/axis/escalations/srv-alpha")
    assert response.status_code == 200, response.text
    server_body = response.json()
    assert server_body["server_id"] == "srv-alpha", server_body
    assert server_body["server_name"] == "Alpha Server", server_body
    assert server_body["count"] == 2, server_body
    assert {item["axis_name"] for item in server_body["escalated_axes"]} == {
        "security",
        "reliability",
    }, server_body
    assert all(
        item["server_id"] == "srv-alpha" for item in server_body["escalated_axes"]
    ), server_body

    response = client.get("/api/axis/escalations/unknown-server")
    assert response.status_code == 404, response.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)