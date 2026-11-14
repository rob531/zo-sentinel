"""Acceptance self-test for the score dispute resolution watch service."""
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    resolved_created_at = now - timedelta(days=4)
    resolved_at = resolved_created_at + timedelta(hours=48)

    session = testing_session()
    session.add_all(
        [
            McpScoreDispute(
                server_id="srv-resolution-1",
                submitted_by="reviewer-1",
                proposed_overall_risk=0.8,
                reason_category="risk_classification",
                explanation="Resolution one",
                status="approved",
                admin_note="Approved after review",
                created_at=resolved_created_at,
                resolved_at=resolved_at,
            ),
            McpScoreDispute(
                server_id="srv-resolution-2",
                submitted_by="reviewer-2",
                proposed_overall_risk=0.6,
                reason_category="risk_classification",
                explanation="Resolution two",
                status="rejected",
                admin_note="Rejected after review",
                created_at=resolved_created_at,
                resolved_at=resolved_at,
            ),
            McpScoreDispute(
                server_id="srv-overdue-open",
                submitted_by="submitter-1",
                proposed_overall_risk=0.7,
                reason_category="evidence",
                explanation="Open dispute awaiting review",
                status="open",
                admin_note=None,
                created_at=now - timedelta(days=10),
                resolved_at=None,
            ),
            McpScoreDispute(
                server_id="srv-unknown-age-open",
                submitted_by="submitter-2",
                proposed_overall_risk=0.5,
                reason_category="other",
                explanation="Open dispute without a creation timestamp",
                status="open",
                admin_note=None,
                created_at=None,
                resolved_at=None,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/disputes/resolution-watch", params={"days": 7})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 7, body
    assert sum(body["status_counts"].values()) == 4, body
    assert body["status_counts"]["open"] == 2, body
    assert body["status_counts"]["approved"] == 1, body
    assert body["status_counts"]["rejected"] == 1, body
    assert body["median_resolution_hours"] == 48, body
    assert body["overdue_unresolved"] == 1, body
    assert body["oldest_open_id"] == 3, body
    assert "generated_at" in body, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)