"""Acceptance self-test for the dispute disposition statistics service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpScoreDispute

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    created_at = datetime(2025, 1, 1, 12, 0, 0)
    session = TestingSession()
    session.add_all(
        [
            McpScoreDispute(
                server_id="srv1",
                submitted_by="admin1",
                proposed_overall_risk="HIGH",
                proposed_axes={"accuracy": 0.9},
                reason_category="policy",
                status="OPEN",
                explanation="Policy mismatch",
                created_at=created_at,
            ),
            McpScoreDispute(
                server_id="srv2",
                submitted_by="admin2",
                proposed_overall_risk="HIGH",
                proposed_axes={"accuracy": 0.8},
                reason_category="ambiguity",
                status="OPEN",
                explanation="Ambiguous assessment",
                created_at=created_at,
            ),
            McpScoreDispute(
                server_id="srv3",
                submitted_by="admin3",
                proposed_overall_risk="MEDIUM",
                proposed_axes={"accuracy": 0.7},
                reason_category="ambiguity",
                status="RESOLVED",
                explanation="Resolved after review",
                created_at=created_at,
                resolved_at=created_at + timedelta(hours=24),
            ),
            McpScoreDispute(
                server_id="srv4",
                submitted_by="admin4",
                proposed_overall_risk="LOW",
                proposed_axes={"accuracy": 0.6},
                reason_category="policy",
                status="REJECTED",
                explanation="Rejected assessment",
                created_at=created_at,
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
        response = client.get("/api/disputes/disposition-stats")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4, body
    assert body["by_status"] == {"OPEN": 2, "RESOLVED": 1, "REJECTED": 1}, body
    assert body["by_reason_category"] == {"policy": 2, "ambiguity": 2}, body
    assert body["proposed_tier_mix"] == {"HIGH": 2, "MEDIUM": 1, "LOW": 1}, body
    assert body["median_resolution_hours"] == 24.0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)