"""Acceptance self-test for the dispute resolution latency service."""
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

    now = datetime.utcnow().replace(microsecond=0)
    first_created = now - timedelta(days=3)
    second_created = now - timedelta(days=2)
    pending_created = now - timedelta(days=1)
    rejected_created = now - timedelta(days=1)

    session = TestingSession()
    try:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="srv-1",
                    submitted_by="reviewer-1",
                    proposed_overall_risk=0.8,
                    reason_category="false_positive",
                    explanation="Seeded resolved dispute",
                    status="resolved",
                    created_at=first_created,
                    resolved_at=first_created + timedelta(hours=2),
                ),
                McpScoreDispute(
                    id=2,
                    server_id="srv-2",
                    submitted_by="reviewer-2",
                    proposed_overall_risk=0.6,
                    reason_category="policy_mismatch",
                    explanation="Seeded resolved dispute",
                    status="resolved",
                    created_at=second_created,
                    resolved_at=second_created + timedelta(hours=40),
                ),
                McpScoreDispute(
                    id=3,
                    server_id="srv-3",
                    submitted_by="reviewer-3",
                    proposed_overall_risk=0.4,
                    reason_category="missing_context",
                    explanation="Seeded pending dispute",
                    status="pending",
                    created_at=pending_created,
                ),
                McpScoreDispute(
                    id=4,
                    server_id="srv-4",
                    submitted_by="reviewer-4",
                    proposed_overall_risk=0.2,
                    reason_category="other",
                    explanation="Seeded rejected dispute",
                    status="rejected",
                    created_at=rejected_created,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/disputes/resolution-latency", params={"days": 30})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["days"] == 30, body
    assert body["total"] == 4, body

    status_counts = {row["status"]: row["count"] for row in body["by_status"]}
    assert status_counts == {
        "resolved": 2,
        "pending": 1,
        "rejected": 1,
    }, body

    assert sum(row["count"] for row in body["by_reason"]) == 4, body

    latency = body["latency_hours"]
    assert latency["resolved_count"] == 2, body
    assert latency["mean"] == 21.0, body
    assert latency["median"] == 21.0, body
    assert latency["max"] == 40.0, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)