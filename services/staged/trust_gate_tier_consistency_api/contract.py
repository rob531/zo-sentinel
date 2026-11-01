"""Acceptance self-test for the trust-gate tier consistency API."""
from __future__ import annotations

import sys


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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="trust-consistency-1",
                name="Consistency Server One",
                url="https://one.example.test",
                risk_tier="HIGH",
                verdict="untrusted",
                trust_score=0.5,
            ),
            McpServerRegistry(
                server_id="trust-consistency-2",
                name="Consistency Server Two",
                url="https://two.example.test",
                risk_tier="HIGH",
                verdict="untrusted",
                trust_score=0.5,
            ),
        ]
    )
    session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="trust-consistency-1",
                axis_name="overall",
                label="trusted",
                model_version="contract-test",
                p_top=0.5,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="trust-consistency-2",
                axis_name="overall",
                label="trusted",
                model_version="contract-test",
                p_top=0.5,
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

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scores/trust-gate-consistency")
        assert response.status_code == 200, response.text
        initial = response.json()
        assert initial["checked_count"] == 2, initial

        initial_divergences = {
            item["server_id"]: item for item in initial["divergences"]
        }

        db = TestingSession()
        try:
            for server_id in ("trust-consistency-1", "trust-consistency-2"):
                divergence = initial_divergences.get(server_id)
                derived_risk = (
                    divergence["derived_published_overall_risk"]
                    if divergence is not None
                    else "HIGH"
                )
                stored_risk = "HIGH" if str(derived_risk).upper() == "LOW" else "LOW"
                server = (
                    db.query(McpServerRegistry)
                    .filter(McpServerRegistry.server_id == server_id)
                    .one()
                )
                server.risk_tier = stored_risk
            db.commit()
        finally:
            db.close()

        response = client.get("/api/scores/trust-gate-consistency")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["checked_count"] == 2, body
    assert body["divergent_count"] == 2, body
    assert len(body["divergences"]) == 2, body
    assert {item["server_id"] for item in body["divergences"]} == {
        "trust-consistency-1",
        "trust-consistency-2",
    }, body
    for divergence in body["divergences"]:
        assert isinstance(divergence["reason"], str), body
        assert divergence["reason"].strip(), body

    known_tiers = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert set(body["tier_distribution"]) <= known_tiers, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)