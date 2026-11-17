"""Acceptance self-test for the decision_rule_version_mix service."""
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
    from app.models import Base, McpLlmAxisScore

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="srv1",
                axis_name="risk",
                model_version="model-v1",
                adapter_sha256="adapter-v1",
                decision_rule_version="v1",
                scored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv2",
                axis_name="risk",
                model_version="model-v1",
                adapter_sha256="adapter-v1",
                decision_rule_version="v1",
                scored_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv3",
                axis_name="risk",
                model_version="model-v1",
                adapter_sha256="adapter-v2",
                decision_rule_version="v2",
                scored_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
        ]
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

    response = TestClient(app).get(
        "/api/scoring/decision-rule-version-mix"
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_rows"] == 3, body
    assert body["distinct_versions"] == 2, body
    assert len(body["versions"]) == 2, body
    assert body["latest_version"] == "v2", body

    versions = {item["decision_rule_version"]: item for item in body["versions"]}
    assert set(versions) == {"v1", "v2"}, body
    assert versions["v1"]["rows"] == 2, body
    assert versions["v1"]["servers"] == 2, body
    assert versions["v2"]["rows"] == 1, body
    assert versions["v2"]["servers"] == 1, body
    assert versions["v1"]["model_versions"] == ["model-v1"], body
    assert versions["v2"]["model_versions"] == ["model-v1"], body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)