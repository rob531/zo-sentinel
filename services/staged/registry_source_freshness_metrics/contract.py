from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.models import McpServerRegistry
    from services.staged.registry_source_freshness_metrics.router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-1",
                    name="Fresh One",
                    registry_source="source-a",
                    last_seen=now - timedelta(hours=1),
                ),
                McpServerRegistry(
                    server_id="stale-1",
                    name="Stale One",
                    registry_source="source-a",
                    last_seen=now - timedelta(hours=25),
                ),
                McpServerRegistry(
                    server_id="week-1",
                    name="Within Week",
                    registry_source="source-b",
                    last_seen=now - timedelta(days=5),
                ),
                McpServerRegistry(
                    server_id="old-1",
                    name="Older One",
                    registry_source="source-c",
                    last_seen=now - timedelta(days=8),
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

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    response = TestClient(app).get("/api/sources/freshness")
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["sources"]) == 3, body

    source_a = next(
        item for item in body["sources"] if item["name"] == "source-a"
    )
    assert source_a["fresh_24h"] == 1, body
    assert source_a["fresh_7d"] == 2, body
    assert source_a["total"] == 2, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"FAIL: {exc!r}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)