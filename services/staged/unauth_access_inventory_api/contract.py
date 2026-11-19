from __future__ import annotations

import sys
from datetime import datetime


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, McpUnauthAccess.__table__],
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    try:
        session.add(
            McpServerRegistry(
                server_id="srv-a-matched",
                name="Matched Server",
                url="https://matched.example/mcp",
                verdict="UNSAFE",
                risk_tier="HIGH",
                trust_score=0.2,
            )
        )
        session.add(
            McpUnauthAccess(
                server_id="srv-a-matched",
                url="https://matched.example/mcp",
                unauth_access="true",
                source="acceptance",
                updated_at=datetime(2025, 1, 3),
            )
        )
        session.add(
            McpUnauthAccess(
                server_id="srv-b-unregistered",
                url="https://unregistered-b.example/mcp",
                unauth_access="true",
                source="acceptance",
                updated_at=datetime(2025, 1, 2),
            )
        )
        session.add(
            McpUnauthAccess(
                server_id="srv-c-unregistered",
                url="https://unregistered-c.example/mcp",
                unauth_access="true",
                source="acceptance",
                updated_at=datetime(2025, 1, 1),
            )
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
    response = client.get("/api/unauth-access/inventory")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3, body
    assert body["limit"] == 100, body
    assert body["offset"] == 0, body
    assert len(body["items"]) == 3, body
    assert body["items"][0]["risk_tier"] is not None, body
    assert any(item["risk_tier"] is None for item in body["items"]), body

    filtered = client.get(
        "/api/unauth-access/inventory",
        params={"tier": "HIGH"},
    )
    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert 0 < filtered_body["total"] < body["total"], filtered_body
    assert all(item["risk_tier"] == "HIGH" for item in filtered_body["items"]), filtered_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)