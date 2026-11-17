"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.<stage>.<name>.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity). A
non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip. That is the deliberate inverse of the 74% Tier-0 degradation
(FU-031) that let presence pass for correctness.

Run:  python -m services.staged.escalated_axis_watch.contract
      ->  prints PASS / FAIL, exits 0/1
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone


VALID_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = TestingSession()
    try:
        session.add(McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="HIGH"))
        session.add(McpServerRegistry(server_id="srv2", name="Beta", risk_tier="MEDIUM"))
        session.add(McpServerRegistry(server_id="srv3", name="Gamma", risk_tier="LOW"))
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="srv1",
                    axis_name="auth_strength",
                    label="authentication",
                    model_version="model-v1",
                    p_top=0.15,
                    p_critical=0.812345,
                    p_danger=0.73,
                    escalated=True,
                    escalated_to="security",
                    scored_at=now,
                    decision_rule_version="v1",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv2",
                    axis_name="capability_breadth",
                    label="capability",
                    model_version="model-v1",
                    p_top=0.10,
                    p_critical=0.6,
                    p_danger=0.5,
                    escalated=True,
                    escalated_to="operations",
                    scored_at=now,
                    decision_rule_version="v1",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="srv3",
                    axis_name="overall_risk",
                    label="overall",
                    model_version="model-v1",
                    p_top=0.05,
                    p_critical=0.4,
                    p_danger=0.3,
                    escalated=True,
                    escalated_to="security",
                    scored_at=now,
                    decision_rule_version="v1",
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="srv1",
                    axis_name="data_sensitivity",
                    label="sensitivity",
                    model_version="model-v1",
                    p_top=0.01,
                    p_critical=0.2,
                    p_danger=0.1,
                    escalated=False,
                    scored_at=now,
                    decision_rule_version="v1",
                ),
                McpLlmAxisScore(
                    id=5,
                    server_id="srv2",
                    axis_name="exploit_surface",
                    label="surface",
                    model_version="model-v1",
                    p_top=0.01,
                    p_critical=0.2,
                    p_danger=0.1,
                    escalated=False,
                    scored_at=now,
                    decision_rule_version="v1",
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
    response = client.get("/api/axes/escalations")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["count"] == 3, body
    escalations = body["escalations"]
    assert len(escalations) == 3, body
    assert escalations[0]["p_critical"] == 0.8123, body
    assert escalations[0]["server_id"] == "srv1", body
    assert all(item["axis_name"] in VALID_AXES for item in escalations), body
    assert all(isinstance(item["p_top"], float) for item in escalations), body
    assert all(isinstance(item["p_critical"], float) for item in escalations), body
    assert all(isinstance(item["p_danger"], float) for item in escalations), body

    filtered_response = client.get(
        "/api/axes/escalations",
        params={"axes": "auth_strength,not_a_valid_axis"},
    )
    assert filtered_response.status_code == 200, filtered_response.text
    filtered_body = filtered_response.json()
    assert filtered_body["count"] == 1, filtered_body
    assert len(filtered_body["escalations"]) == 1, filtered_body
    assert filtered_body["escalations"][0]["axis_name"] == "auth_strength", filtered_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)