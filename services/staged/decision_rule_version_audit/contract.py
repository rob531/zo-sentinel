"""Acceptance self-test for the decision-rule-version audit service."""

from __future__ import annotations

import sys


def run() -> bool:
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-version-1",
                    name="Version One",
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="srv-version-2",
                    name="Version Two",
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="srv-version-3",
                    name="Version Three",
                    risk_tier="LOW",
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=101,
                    server_id="srv-version-1",
                    axis_name="overall_risk",
                    label="high",
                    label_index=3,
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=False,
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    adapter_sha256="adapter-v1",
                ),
                McpLlmAxisScore(
                    id=102,
                    server_id="srv-version-1",
                    axis_name="auth_strength",
                    label="medium",
                    label_index=2,
                    p_top=0.6,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=False,
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    adapter_sha256="adapter-v1",
                ),
                McpLlmAxisScore(
                    id=103,
                    server_id="srv-version-2",
                    axis_name="overall_risk",
                    label="low",
                    label_index=1,
                    p_top=0.3,
                    p_critical=0.0,
                    p_danger=0.1,
                    escalated=False,
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    adapter_sha256="adapter-v1",
                ),
                McpLlmAxisScore(
                    id=104,
                    server_id="srv-version-3",
                    axis_name="network_egress",
                    label="high",
                    label_index=3,
                    p_top=0.7,
                    p_critical=0.2,
                    p_danger=0.2,
                    escalated=True,
                    escalated_to="security",
                    decision_rule_version="rule-v2",
                    model_version="model-v2",
                    adapter_sha256="adapter-v2",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/scores/decision-rule-versions")
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {"versions"}, body
    versions = body["versions"]
    assert len(versions) == 2, body

    groups = {
        (
            item["decision_rule_version"],
            item["model_version"],
            item["adapter_sha256"],
        ): item
        for item in versions
    }
    assert set(groups) == {
        ("rule-v1", "model-v1", "adapter-v1"),
        ("rule-v2", "model-v2", "adapter-v2"),
    }, body

    first = groups[("rule-v1", "model-v1", "adapter-v1")]
    assert first["rows"] == 3, body
    assert first["servers"] == 2, body

    second = groups[("rule-v2", "model-v2", "adapter-v2")]
    assert second["rows"] == 1, body
    assert second["servers"] == 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)