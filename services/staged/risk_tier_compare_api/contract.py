"""Acceptance self-test for the risk tier comparison API."""
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

    axes = (
        ("overall_risk", "MEDIUM", 2, 0.40),
        ("auth_strength", "HIGH", 3, 0.80),
        ("capability_breadth", "MEDIUM", 2, 0.55),
        ("data_sensitivity", "LOW", 1, 0.20),
        ("network_egress", "MEDIUM", 2, 0.45),
        ("maintainer_trust", "HIGH", 3, 0.75),
        ("exploit_surface", "LOW", 1, 0.25),
    )

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    testing_session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = testing_session()
    session.add(
        McpServerRegistry(
            server_id="A",
            name="Server A",
            url="https://a.example.test",
            verdict="ALLOW",
            risk_tier="MEDIUM",
            trust_score=0.72,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="B",
            name="Server B",
            url="https://b.example.test",
            verdict="ALLOW",
            risk_tier="MEDIUM",
            trust_score=0.72,
        )
    )

    for server_id in ("A", "B"):
        for index, (axis_name, label, label_index, p_top) in enumerate(axes):
            session.add(
                McpLlmAxisScore(
                    id=f"{server_id}-{axis_name}",
                    server_id=server_id,
                    axis_name=axis_name,
                    label=label,
                    label_index=label_index,
                    p_top=p_top,
                    p_critical=0.05,
                    p_danger=0.15,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="test-rule-v1",
                    model_version="test-model-v1",
                )
            )
    session.commit()
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
    response = client.get("/api/risk/compare", params={"server_ids": "A,B"})
    assert response.status_code == 200, response.text

    body = response.json()
    servers = body["servers"]
    assert {server["server_id"] for server in servers} == {"A", "B"}, body

    expected_axes = {axis_name for axis_name, _, _, _ in axes}
    for server in servers:
        assert set(server["axes"]) == expected_axes, server
        assert server["name"] in {"Server A", "Server B"}, server
        assert server["risk_tier"], server
        assert "overall" in server, server
        assert "criteria_version" in server, server

    comparison = body["comparison"]
    assert set(comparison) == expected_axes, comparison
    assert comparison["overall_risk"]["a_label"] == "MEDIUM", comparison
    assert comparison["overall_risk"]["b_label"] == "MEDIUM", comparison
    assert comparison["overall_risk"]["delta"] == 0, comparison

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)