"""Acceptance self-test for the score dispute ageing service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


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

    now = datetime.now(timezone.utc)
    session = TestingSession()
    session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id="srv-ageing",
                status="open",
                reason_category="risk",
                proposed_overall_risk=0.8,
                explanation="created now",
                submitted_by="acceptance@example.invalid",
                created_at=now,
            ),
            McpScoreDispute(
                id=2,
                server_id="srv-ageing",
                status="open",
                reason_category="risk",
                proposed_overall_risk=0.7,
                explanation="created three days ago",
                submitted_by="acceptance@example.invalid",
                created_at=now - timedelta(days=3),
            ),
            McpScoreDispute(
                id=3,
                server_id="srv-ageing",
                status="open",
                reason_category="capability",
                proposed_overall_risk=0.6,
                explanation="created forty days ago",
                submitted_by="acceptance@example.invalid",
                created_at=now - timedelta(days=40),
            ),
            McpScoreDispute(
                id=4,
                server_id="srv-ageing",
                status="resolved",
                reason_category="risk",
                proposed_overall_risk=0.5,
                explanation="resolved dispute",
                submitted_by="acceptance@example.invalid",
                created_at=now - timedelta(days=2),
                resolved_at=now,
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

    client = TestClient(app)
    response = client.get("/api/disputes/ageing")
    assert response.status_code == 200, response.text

    body = response.json()
    buckets = {item["bucket"]: item["count"] for item in body["buckets"]}
    assert set(buckets) == {"0-1d", "1-7d", "7-30d", "30d+"}, body
    assert buckets["30d+"] == 1, body
    assert body["totals"]["total"] == 4, body
    assert body["totals"]["open"] == 3, body
    assert body["totals"]["resolved"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)