"""Acceptance self-test for the scoring pipeline version drift API."""
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(server_id="srv1", name="Server One"),
                McpServerRegistry(server_id="srv2", name="Server Two"),
                McpServerRegistry(server_id="srv3", name="Server Three"),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    model_version="model-v1",
                    adapter_sha256="adapter-sha",
                    decision_rule_version="rule-old",
                    server_id="srv1",
                    axis_name="security",
                    scored_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=2,
                    model_version="model-v1",
                    adapter_sha256="adapter-sha",
                    decision_rule_version="rule-new",
                    server_id="srv2",
                    axis_name="security",
                    scored_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=3,
                    model_version="model-v1",
                    adapter_sha256="adapter-sha",
                    decision_rule_version="rule-new",
                    server_id="srv3",
                    axis_name="security",
                    scored_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
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

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/scoring/version-drift")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "as_of" in body, body
    assert len(body["versions"]) == 2, body
    assert body["drifted_server_count"] == 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)