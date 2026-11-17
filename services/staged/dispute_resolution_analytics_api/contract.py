"""Acceptance self-test for the dispute resolution analytics API.

Run with:
    python -m services.staged.dispute_resolution_analytics_api.contract
"""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import McpScoreDispute

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine, checkfirst=True)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = testing_session()
    session.add_all(
        [
            McpScoreDispute(
                id=101,
                server_id="srv-alpha",
                submitted_by="user-alice",
                proposed_overall_risk=0.80,
                proposed_axes={"security": 0.9, "quality": 0.7},
                reason_category="risk_misclassification",
                explanation="The proposed risk score is too high.",
                status="PENDING",
                admin_note=None,
                created_at=now - timedelta(days=5),
                resolved_at=None,
            ),
            McpScoreDispute(
                id=102,
                server_id="srv-beta",
                submitted_by="user-bob",
                proposed_overall_risk=0.35,
                proposed_axes={"security": 0.3, "quality": 0.4},
                reason_category="missing_context",
                explanation="Repository context was omitted.",
                status="RESOLVED",
                admin_note="Score adjusted.",
                created_at=now - timedelta(days=4),
                resolved_at=now - timedelta(days=4, hours=-2),
            ),
            McpScoreDispute(
                id=103,
                server_id="srv-gamma",
                submitted_by="user-carol",
                proposed_overall_risk=0.55,
                proposed_axes={"security": 0.6, "quality": 0.5},
                reason_category="axis_disagreement",
                explanation="The axis proposals disagree.",
                status="PENDING",
                admin_note=None,
                created_at=now - timedelta(days=2),
                resolved_at=None,
            ),
            McpScoreDispute(
                id=104,
                server_id="srv-delta",
                submitted_by="user-dave",
                proposed_overall_risk=0.20,
                proposed_axes={"security": 0.2, "quality": 0.2},
                reason_category="risk_misclassification",
                explanation="The proposed risk score is too low.",
                status="RESOLVED",
                admin_note="Score corrected.",
                created_at=now - timedelta(days=1),
                resolved_at=now - timedelta(days=1, hours=-4),
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

    with TestClient(app) as client:
        response = client.get("/api/disputes/analytics")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["by_status"].get("PENDING") == 2, body
        assert body["by_status"].get("RESOLVED") == 2, body
        assert body["by_reason_category"].get("risk_misclassification") == 2, body
        assert body["by_reason_category"].get("missing_context") == 1, body
        assert body["by_reason_category"].get("axis_disagreement") == 1, body
        assert isinstance(body["median_resolution_hours"], float), body
        assert body["median_resolution_hours"] >= 0.0, body
        assert 0.0 <= body["resolution_rate"] <= 1.0, body
        assert len(body["recent"]) == 4, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 - fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)