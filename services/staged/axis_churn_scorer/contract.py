from __future__ import annotations

import sys
from datetime import datetime, timedelta

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    oldest = now - timedelta(days=2) + timedelta(minutes=1)
    middle = now - timedelta(days=1)

    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="1", name="Alpha", scan_count=10),
                McpServerRegistry(server_id="2", name="Beta", scan_count=20),
                McpServerRegistry(server_id="3", name="Gamma", scan_count=10),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="1",
                    axis_name="test-axis",
                    model_version="alpha-v1",
                    label_index=0,
                    scored_at=oldest,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="1",
                    axis_name="test-axis",
                    model_version="alpha-v2",
                    label_index=1,
                    scored_at=middle,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="1",
                    axis_name="test-axis",
                    model_version="alpha-v3",
                    label_index=2,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="2",
                    axis_name="test-axis",
                    model_version="beta-v1",
                    label_index=0,
                    scored_at=oldest,
                ),
                McpLlmAxisScore(
                    id=5,
                    server_id="2",
                    axis_name="test-axis",
                    model_version="beta-v2",
                    label_index=1,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=6,
                    server_id="3",
                    axis_name="test-axis",
                    model_version="gamma-v1",
                    label_index=0,
                    scored_at=oldest,
                ),
                McpLlmAxisScore(
                    id=7,
                    server_id="3",
                    axis_name="test-axis",
                    model_version="gamma-v2",
                    label_index=0,
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/axis-churn?days=2")

    assert response.status_code == 200, response.text
    servers = response.json()["servers"]
    assert len(servers) == 3, servers
    assert servers[0]["server_id"] == 1, servers
    assert servers[0]["name"] == "Alpha", servers[0]
    assert servers[0]["axis_change_count"] == 2, servers[0]
    assert servers[0]["scan_count"] == 10, servers[0]
    assert abs(servers[0]["churn_rate"] - 0.2) < 1e-9, servers[0]
    assert [server["churn_rate"] for server in servers] == sorted(
        (server["churn_rate"] for server in servers), reverse=True
    ), servers
    engine.dispose()
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)
