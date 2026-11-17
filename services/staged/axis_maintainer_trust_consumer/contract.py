"""Acceptance self-test for the maintainer_trust scoring consumer."""
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
                    server_id="trust-1",
                    name="Maintainer One",
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="trust-2",
                    name="Maintainer Two",
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="trust-3",
                    name="Maintainer Three",
                    risk_tier="LOW",
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="trust-1",
                    axis_name="maintainer_trust",
                    label="critical",
                    label_index=0,
                    p_top=0.75,
                    p_critical=0.60,
                    p_danger=0.05,
                    escalated=True,
                    escalated_to="HIGH",
                    decision_rule_version="v1",
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="trust-2",
                    axis_name="maintainer_trust",
                    label="elevated",
                    label_index=1,
                    p_top=0.45,
                    p_critical=0.10,
                    p_danger=0.20,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="v1",
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="trust-3",
                    axis_name="maintainer_trust",
                    label="baseline",
                    label_index=2,
                    p_top=0.20,
                    p_critical=0.01,
                    p_danger=0.00,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="v1",
                    model_version="test-model",
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
    response = client.get("/api/axis/maintainer_trust")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["axis"] == "maintainer_trust", body
    assert body["count"] == 3, body
    assert len(body["rows"]) == 3, body

    rows = {row["server_id"]: row for row in body["rows"]}
    assert rows["trust-1"]["p_top"] == 0.75, body
    assert rows["trust-1"]["risk_tier"] == "HIGH", body
    assert rows["trust-2"]["risk_tier"] == "MEDIUM", body
    assert rows["trust-3"]["risk_tier"] == "LOW", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)