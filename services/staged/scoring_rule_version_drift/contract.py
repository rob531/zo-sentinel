from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta

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

    now = datetime.utcnow()
    session = TestingSession()
    session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="srv1",
                axis_name="critical",
                decision_rule_version="rule-v1",
                model_version="model-v1",
                adapter_sha256="a" * 64,
                scored_at=now - timedelta(minutes=3),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv1",
                axis_name="danger",
                decision_rule_version="rule-v1",
                model_version="model-v1",
                adapter_sha256="a" * 64,
                scored_at=now - timedelta(minutes=2),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv2",
                axis_name="critical",
                decision_rule_version="rule-v2",
                model_version="model-v2",
                adapter_sha256="b" * 64,
                scored_at=now - timedelta(minutes=1),
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

    client = TestClient(app)
    response = client.get("/api/scoring/rule-versions", params={"days": 30})

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["days"] == 30, body
    assert body["version_count"] == 2, body
    assert body["current_version"] == {
        "decision_rule_version": "rule-v2",
        "model_version": "model-v2",
        "adapter_sha256": "b" * 64,
    }, body
    assert len(body["versions"]) == 3, body
    assert all(version["score_count"] > 0 for version in body["versions"]), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)