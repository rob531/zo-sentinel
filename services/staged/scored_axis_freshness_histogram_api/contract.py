from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


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
    testing_session = sessionmaker(bind=engine, autoflush=False)

    now = datetime.now(timezone.utc)
    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(server_id="srv1", name="A", risk_tier="HIGH"),
                McpServerRegistry(server_id="srv2", name="B", risk_tier="HIGH"),
                McpServerRegistry(server_id="srv3", name="C", risk_tier="LOW"),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="srv1",
                    axis_name="overall_risk",
                    model_version="test",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv2",
                    axis_name="overall_risk",
                    model_version="test",
                    scored_at=now - timedelta(days=10),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="srv3",
                    axis_name="overall_risk",
                    model_version="test",
                    scored_at=now - timedelta(days=120),
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
    response = client.get("/api/scores/freshness-histogram?days=365")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 365, body
    buckets = {item["bucket"] for item in body["buckets"]}
    assert "0-1d" in buckets, body
    assert "90d+" in buckets, body
    assert body["stale_servers"] >= 1, body
    assert body["total_scored_rows"] == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)