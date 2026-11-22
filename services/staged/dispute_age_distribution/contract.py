"""Acceptance self-test for the dispute age distribution service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta, timezone

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

    now = datetime.now(timezone.utc)
    session = TestingSession()
    try:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="srv-age-test",
                    submitted_by="user-age-test",
                    proposed_overall_risk=0.25,
                    proposed_axes={},
                    explanation="New open dispute",
                    reason_category="scoring",
                    status="open",
                    created_at=now,
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=2,
                    server_id="srv-age-test",
                    submitted_by="user-age-test",
                    proposed_overall_risk=0.50,
                    proposed_axes={},
                    explanation="Two-day old open dispute",
                    reason_category="scoring",
                    status="open",
                    created_at=now - timedelta(days=2),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=3,
                    server_id="srv-age-test",
                    submitted_by="user-age-test",
                    proposed_overall_risk=0.75,
                    proposed_axes={},
                    explanation="Resolved dispute",
                    reason_category="evidence",
                    status="resolved",
                    created_at=now - timedelta(days=10),
                    resolved_at=now - timedelta(days=5),
                ),
                McpScoreDispute(
                    id=4,
                    server_id="srv-age-test",
                    submitted_by="user-age-test",
                    proposed_overall_risk=0.90,
                    proposed_axes={},
                    explanation="Rejected dispute",
                    reason_category="evidence",
                    status="rejected",
                    created_at=now - timedelta(days=120),
                    resolved_at=now - timedelta(days=100),
                ),
            ]
        )
        session.commit()
    finally:
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
    response = client.get("/api/disputes/age-distribution")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 4, body
    assert body["open"] == 2, body
    assert body["closed"] == 2, body

    buckets = {item["bucket"]: item for item in body["buckets"]}
    assert buckets["2-7"]["open"] == 1, body
    assert buckets["2-7"]["closed"] == 0, body

    median = body["median_open_age_days"]
    assert isinstance(median, float), body
    assert median > 0, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)