from __future__ import annotations

import sys


def run() -> bool:
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
                axis_name="security",
                decision_rule_version="v1",
                model_version="model-a",
                escalated=False,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv2",
                axis_name="quality",
                decision_rule_version="v1",
                model_version="model-a",
                escalated=False,
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv3",
                axis_name="security",
                decision_rule_version="v2",
                model_version="model-a",
                escalated=False,
            ),
            McpLlmAxisScore(
                id=4,
                server_id="srv3",
                axis_name="quality",
                decision_rule_version="v2",
                model_version="model-a",
                escalated=False,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/axis/decision-rule-versions")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["versions"]) == 2, body
    versions = {item["decision_rule_version"]: item for item in body["versions"]}
    assert versions["v1"]["servers_scored"] == 2, body
    assert versions["v1"]["axis_count"] == 2, body
    assert versions["v2"]["servers_scored"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)