"""Acceptance self-test for the watchlist snapshot API."""
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
    from app.models import Base, McpLlmAxisScore, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="trusted-one",
                name="Trusted One",
                url="https://trusted-one.example",
                risk_tier="trusted",
                verdict="trusted",
                trust_score=0.99,
                confidence=0.99,
                last_assessed=datetime(2025, 1, 1),
                last_scanned=datetime(2025, 1, 1),
                scan_count=10,
            ),
            McpServerRegistry(
                server_id="trusted-two",
                name="Trusted Two",
                url="https://trusted-two.example",
                risk_tier="trusted",
                verdict="trusted",
                trust_score=0.98,
                confidence=0.98,
                last_assessed=datetime(2025, 1, 1),
                last_scanned=datetime(2025, 1, 1),
                scan_count=9,
            ),
            McpServerRegistry(
                server_id="medium-server",
                name="Medium Server",
                url="https://medium.example",
                risk_tier="MEDIUM",
                verdict="review",
                trust_score=0.2,
                confidence=0.8,
                last_assessed=datetime(2025, 1, 1),
                last_scanned=datetime(2025, 1, 1),
                scan_count=4,
            ),
            McpServerRegistry(
                server_id="high-server",
                name="High Server",
                url="https://high.example",
                risk_tier="HIGH",
                verdict="malicious",
                trust_score=0.1,
                confidence=0.95,
                last_assessed=datetime(2025, 1, 2),
                last_scanned=datetime(2025, 1, 2),
                scan_count=5,
            ),
        ]
    )
    session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="medium-server",
                axis_name="overall_risk",
                model_version="contract-test",
                label="MEDIUM",
                label_index=2,
                scored_at=datetime(2025, 1, 1),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="high-server",
                axis_name="overall_risk",
                model_version="contract-test",
                label="HIGH",
                label_index=3,
                scored_at=datetime(2025, 1, 2),
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
    response = client.get("/api/watchlist/snapshot", params={"limit": 50})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["count"] == 2, body
    assert body["servers"][0]["server_id"] == "high-server", body
    assert all(server["risk_tier"] != "trusted" for server in body["servers"]), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)