"""Acceptance self-test for score provenance lineage."""
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    old_scored_at = datetime.utcnow() - timedelta(days=40)
    recent_scored_at = datetime.utcnow() - timedelta(days=2)

    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(server_id="srv1", name="Server One", risk_tier="HIGH"),
                McpServerRegistry(server_id="srv2", name="Server Two", risk_tier="LOW"),
                McpLlmAxisScore(
                    id=1,
                    server_id="srv1",
                    axis_name="critical",
                    label="critical",
                    p_top=0.91,
                    model_version="model-v1",
                    adapter_sha256="adapter-v1",
                    decision_rule_version="rules-v1",
                    scored_at=old_scored_at,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv1",
                    axis_name="danger",
                    label="danger",
                    p_top=0.74,
                    model_version="model-v1",
                    adapter_sha256="adapter-v1",
                    decision_rule_version="rules-v1",
                    scored_at=old_scored_at,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="srv1",
                    axis_name="critical",
                    label="critical",
                    p_top=0.63,
                    model_version="model-v2",
                    adapter_sha256="adapter-v2",
                    decision_rule_version="rules-v2",
                    scored_at=recent_scored_at,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="srv2",
                    axis_name="critical",
                    label="critical",
                    p_top=0.42,
                    model_version="model-v3",
                    adapter_sha256="adapter-v3",
                    decision_rule_version="rules-v3",
                    scored_at=recent_scored_at,
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
    response = client.get("/api/scores/provenance", params={"server_id": "srv1"})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["server_id"] == "srv1", body
    assert body["risk_tier"] == "HIGH", body
    assert body["stale"] is True, body
    assert body["stale_days"] == 30, body
    assert len(body["models"]) == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)