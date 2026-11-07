"""Contract acceptance self-test for the trust score distribution service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

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
            McpServerRegistry(
                server_id="trust-contract-01",
                name="Contract Server 01",
                registry_source="source-a",
                risk_tier="LOW",
                trust_score=0.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-02",
                name="Contract Server 02",
                registry_source="source-a",
                risk_tier="LOW",
                trust_score=1.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-03",
                name="Contract Server 03",
                registry_source="source-a",
                risk_tier="MEDIUM",
                trust_score=2.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-04",
                name="Contract Server 04",
                registry_source="source-a",
                risk_tier="MEDIUM",
                trust_score=3.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-05",
                name="Contract Server 05",
                registry_source="source-a",
                risk_tier="MEDIUM",
                trust_score=4.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-06",
                name="Contract Server 06",
                registry_source="source-b",
                risk_tier="HIGH",
                trust_score=5.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-07",
                name="Contract Server 07",
                registry_source="source-b",
                risk_tier="HIGH",
                trust_score=6.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-08",
                name="Contract Server 08",
                registry_source="source-b",
                risk_tier="HIGH",
                trust_score=7.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-09",
                name="Contract Server 09",
                registry_source="source-b",
                risk_tier="HIGH",
                trust_score=8.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-10",
                name="Contract Server 10",
                registry_source="source-b",
                risk_tier="CRITICAL",
                trust_score=9.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-11",
                name="Contract Server 11",
                registry_source="source-b",
                risk_tier="CRITICAL",
                trust_score=10.0,
            ),
            McpServerRegistry(
                server_id="trust-contract-12",
                name="Contract Server 12",
                registry_source="source-b",
                risk_tier="UNKNOWN",
                trust_score=None,
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

    with TestClient(app) as client:
        response = client.get("/api/registry/trust-score-distribution?bucket=10")
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["total"] == 12, body
        assert body["never_scored"] >= 1, body
        assert sum(item["count"] for item in body["buckets"]) == (
            body["total"] - body["never_scored"]
        ), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)