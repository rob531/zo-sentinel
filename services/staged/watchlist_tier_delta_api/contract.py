"""Acceptance self-test for the watchlist tier-delta API."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import McpLlmAxisScore, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add(
        McpServerRegistry(
            server_id="a",
            name="Healthy server",
            risk_tier="LOW",
            verdict="VERIFIED",
            trust_score=0.95,
            confidence=0.99,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="b",
            name="Escalated server",
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="ESCALATED",
            trust_score=0.42,
            confidence=0.88,
        )
    )
    session.add(
        McpLlmAxisScore(
            id=1,
            server_id="b",
            axis_name="auth_strength",
            model_version="contract-test",
            p_top=0.91,
            p_critical=0.91,
            escalated=True,
            escalated_to="HIGH_RISK_ISOLATED",
        )
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
    response = client.get("/api/watchlist/tier-delta?server_ids=a,b,c")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["count"] == 2, body

    servers = {server["server_id"]: server for server in body["servers"]}
    assert set(servers) == {"a", "b"}, body

    escalated = servers["b"]
    assert escalated["risk_tier"] == "HIGH_RISK_ISOLATED", escalated
    assert escalated["verdict"] == "ESCALATED", escalated
    assert "auth_strength" in escalated["critical_axes"], escalated

    auth_strength = next(
        axis for axis in escalated["axes"] if axis["axis_name"] == "auth_strength"
    )
    assert auth_strength["critical_flag"] is True, auth_strength
    assert auth_strength["escalated"] is True, auth_strength
    assert auth_strength["escalated_to"] == "HIGH_RISK_ISOLATED", auth_strength
    assert auth_strength["p_critical"] > 0.5, auth_strength

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)