"""Acceptance self-test for the score dispute pipeline health service."""
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
    from app.models import Base, McpScoreDispute, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    now = datetime.utcnow()
    session.add_all(
        [
            McpServerRegistry(server_id="srv1", name="Server One", risk_tier="HIGH"),
            McpServerRegistry(server_id="srv2", name="Server Two", risk_tier="LOW"),
        ]
    )
    session.add_all(
        [
            McpScoreDispute(
                server_id="srv1",
                status="open",
                reason_category="risk_misclassification",
                proposed_overall_risk=80,
                submitted_by="analyst-1",
                explanation="Risk score requires review.",
                created_at=now - timedelta(days=10),
            ),
            McpScoreDispute(
                server_id="srv1",
                status="open",
                reason_category="missing_context",
                proposed_overall_risk=60,
                submitted_by="analyst-1",
                explanation="Additional context is needed.",
                created_at=now - timedelta(days=6),
            ),
            McpScoreDispute(
                server_id="srv2",
                status="open",
                reason_category="risk_misclassification",
                proposed_overall_risk=40,
                submitted_by="analyst-2",
                explanation="Risk score requires review.",
                created_at=now - timedelta(days=3),
            ),
            McpScoreDispute(
                server_id="srv2",
                status="under_review",
                reason_category="model_disagreement",
                proposed_overall_risk=55,
                submitted_by="analyst-2",
                explanation="The disagreement is under review.",
                created_at=now - timedelta(days=2),
            ),
            McpScoreDispute(
                server_id="srv1",
                status="resolved",
                reason_category="missing_context",
                proposed_overall_risk=70,
                submitted_by="analyst-1",
                explanation="The dispute was resolved.",
                created_at=now - timedelta(days=20),
                resolved_at=now - timedelta(days=1),
            ),
        ]
    )
    session.commit()
    session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)
    response = client.get("/api/disputes/pipeline-health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5, body
    assert body["by_status"]["open"] == 3, body
    assert body["unresolved"] == 4, body
    assert isinstance(body["median_age_days"], float), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)