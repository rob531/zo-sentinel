"""Acceptance self-test for the verdict_reasoning_api service."""
from __future__ import annotations

import sys
from datetime import datetime, timezone


AXIS_NAMES = (
    "accuracy",
    "safety",
    "security",
    "privacy",
    "reliability",
    "transparency",
    "alignment",
)


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
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    name="Server One",
                    verdict="ALLOW",
                    verdict_reasoning="The server passed all seven reasoning axes.",
                    confidence=0.94,
                    risk_tier="LOW",
                    trust_score=0.91,
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    name="Server Two",
                    verdict="REVIEW",
                    verdict_reasoning="The server has not been assessed on all axes.",
                    confidence=0.62,
                    risk_tier="MEDIUM",
                    trust_score=0.67,
                    last_assessed=now,
                ),
            ]
        )
        session.flush()

        for index, axis_name in enumerate(AXIS_NAMES, start=1):
            session.add(
                McpLlmAxisScore(
                    id=index,
                    server_id="srv-1",
                    axis_name=axis_name,
                    label=axis_name,
                    p_top=0.8 + index / 100,
                    p_critical=0.01,
                    p_danger=0.04,
                    escalated=False,
                    escalated_to=None,
                    scored_at=now,
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                )
            )

        session.add(
            McpLlmAxisScore(
                id=100,
                server_id="unrelated-server",
                axis_name="unrelated_axis",
                label="Unrelated",
                p_top=0.5,
                p_critical=0.5,
                p_danger=0.5,
                escalated=False,
                escalated_to=None,
                scored_at=now,
                decision_rule_version="rules-v1",
                model_version="model-v1",
            )
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

    response = client.get("/api/servers/srv-1/reasoning")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_id"] == "srv-1", body
    assert body["axis_count"] == 7, body
    assert set(body["axes"]) == set(AXIS_NAMES), body
    assert "unrelated_axis" not in body["axes"], body

    response = client.get("/api/servers/srv-2/reasoning")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_id"] == "srv-2", body
    assert body["axis_count"] == 0, body
    assert body["axes"] == {}, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)