"""Acceptance self-test for the decision rule version audit API."""
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
                McpServerRegistry(server_id="srv1", name="A", risk_tier="HIGH"),
                McpServerRegistry(server_id="srv2", name="B", risk_tier="HIGH"),
                McpServerRegistry(server_id="srv3", name="C", risk_tier="LOW"),
            ]
        )
        session.flush()

        now = datetime(2024, 1, 1, 12, 0, 0)
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="srv1",
                    axis_name="security",
                    decision_rule_version="v1",
                    model_version="model-a",
                    adapter_sha256="adapter-1",
                    escalated=True,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv2",
                    axis_name="security",
                    decision_rule_version="v1",
                    model_version="model-a",
                    adapter_sha256="adapter-1",
                    escalated=False,
                    scored_at=now + timedelta(minutes=1),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="srv3",
                    axis_name="security",
                    decision_rule_version="v2",
                    model_version="model-b",
                    adapter_sha256="adapter-2",
                    escalated=False,
                    scored_at=now + timedelta(minutes=2),
                ),
            ]
        )
        session.commit()
    finally:
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
    response = client.get("/api/scoring/decision-rule-audit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "versions" in body, body

    versions = body["versions"]
    assert len(versions) == 2, body
    assert {item["decision_rule_version"] for item in versions} == {"v1", "v2"}, body

    v1 = next(item for item in versions if item["decision_rule_version"] == "v1")
    assert v1["row_count"] == 2, body
    assert v1["server_count"] == 2, body
    assert v1["escalated_rows"] == 1, body
    assert v1["escalated_share"] == 1 / 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)