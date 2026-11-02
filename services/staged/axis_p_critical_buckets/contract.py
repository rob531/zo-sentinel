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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="srv1",
                axis_name="safety",
                label="low",
                label_index=0,
                probs=[0.995, 0.005],
                p_top=0.995,
                p_critical=0.005,
                p_danger=0.0,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="test",
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv2",
                axis_name="safety",
                label="medium",
                label_index=1,
                probs=[0.98, 0.02],
                p_top=0.98,
                p_critical=0.02,
                p_danger=0.01,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="test",
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv3",
                axis_name="safety",
                label="high",
                label_index=2,
                probs=[0.9, 0.1],
                p_top=0.9,
                p_critical=0.1,
                p_danger=0.05,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="test",
            ),
            McpLlmAxisScore(
                id=4,
                server_id="srv1",
                axis_name="quality",
                label="low",
                label_index=0,
                probs=[0.7, 0.3],
                p_top=0.7,
                p_critical=0.3,
                p_danger=0.1,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="test",
            ),
            McpLlmAxisScore(
                id=5,
                server_id="srv2",
                axis_name="quality",
                label="medium",
                label_index=1,
                probs=[0.5, 0.5],
                p_top=0.5,
                p_critical=0.5,
                p_danger=0.2,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="test",
            ),
            McpLlmAxisScore(
                id=6,
                server_id="srv3",
                axis_name="quality",
                label="high",
                label_index=2,
                probs=[0.2, 0.8],
                p_top=0.2,
                p_critical=0.8,
                p_danger=0.6,
                escalated=True,
                escalated_to="security",
                decision_rule_version="v1",
                model_version="test",
            ),
        ]
    )
    session.commit()
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
    response = client.get("/api/axes/p-critical-buckets")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_rows"] == 6, body
    assert sum(item["count"] for item in body["buckets"]) == 6, body
    assert body["escalated_total"] == 1, body
    assert len(body["by_axis"]) == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)