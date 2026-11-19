"""Acceptance self-test for the unauthenticated-access exposure rollup service."""
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

    now = datetime.now(timezone.utc)
    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-high",
                name="High Risk Server",
                risk_tier="HIGH",
                verdict="EXPOSED",
            ),
            McpServerRegistry(
                server_id="srv-read",
                name="Read Only Server",
                risk_tier="MEDIUM",
                verdict="PARTIAL",
            ),
            McpServerRegistry(
                server_id="srv-write",
                name="Write Server",
                risk_tier="LOW",
                verdict="SAFE",
            ),
            McpUnauthAccess(
                server_id="srv-high",
                url="https://high.example/mcp",
                unauth_access="read_write",
                source="contract-test",
                updated_at=now,
            ),
            McpUnauthAccess(
                server_id="srv-read",
                url="https://read.example/mcp",
                unauth_access="read_only",
                source="contract-test",
                updated_at=now - timedelta(minutes=1),
            ),
            McpUnauthAccess(
                server_id="srv-write",
                url="https://write.example/mcp",
                unauth_access="read_write",
                source="contract-test",
                updated_at=now - timedelta(minutes=2),
            ),
        ]
    )
    session.commit()
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
    response = client.get("/api/exposure/unauth-access-rollup")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_servers"] == 3, body

    modes = {item["mode"]: item for item in body["by_mode"]}
    assert set(modes) == {"read_only", "read_write"}, body
    assert modes["read_only"]["servers"] == 1, body
    assert modes["read_write"]["servers"] == 2, body
    assert modes["read_only"]["high_risk_servers"] == 0, body
    assert modes["read_write"]["high_risk_servers"] == 1, body

    worst_servers = body["worst_servers"]
    assert any(server["server_id"] == "srv-high" for server in worst_servers), body
    assert worst_servers[0]["server_id"] == "srv-high", body
    assert worst_servers[0]["risk_tier"] == "HIGH", body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)