"""Acceptance self-test for the score dispute resolution ledger API."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpScoreDispute, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    session.add(
        McpServerRegistry(
            server_id="srv-1",
            name="Example Server",
            risk_tier="HIGH_RISK_ISOLATED",
        )
    )
    session.add(
        McpScoreDispute(
            server_id="srv-1",
            submitted_by="analyst@example.com",
            proposed_overall_risk="TRUSTED_GENERAL",
            reason_category="false_positive",
            explanation="The disputed score should be reconsidered.",
            status="OPEN",
        )
    )
    session.add(
        McpScoreDispute(
            server_id="srv-1",
            submitted_by="reviewer@example.com",
            proposed_overall_risk="HIGH_RISK_ISOLATED",
            reason_category="confirmed_risk",
            explanation="The risk assessment was confirmed.",
            status="RESOLVED",
        )
    )
    session.add(
        McpScoreDispute(
            server_id="srv-missing",
            submitted_by="analyst@example.com",
            proposed_overall_risk="TRUSTED_GENERAL",
            reason_category="missing_registry",
            explanation="No registry record exists for this server.",
            status="OPEN",
        )
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
    response = client.get("/api/disputes/ledger")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3, body
    assert len(body["items"]) == 3, body
    assert any(item["tier_delta"] == "unknown" for item in body["items"]), body

    open_response = client.get("/api/disputes/ledger", params={"status": "OPEN"})
    assert open_response.status_code == 200, open_response.text
    open_body = open_response.json()
    assert open_body["total"] == 2, open_body
    assert all(item["status"] == "OPEN" for item in open_body["items"]), open_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)