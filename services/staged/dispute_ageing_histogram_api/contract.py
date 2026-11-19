"""Acceptance self-test for the dispute ageing histogram API."""
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

    now = datetime.utcnow().replace(microsecond=0)
    session = TestingSession()
    session.add(McpServerRegistry(server_id="srv1", name="A", risk_tier="HIGH"))
    session.add_all(
        [
            McpScoreDispute(
                server_id="srv1",
                submitted_by="analyst-1",
                proposed_overall_risk=0.9,
                explanation="Open dispute retained for ageing analysis.",
                reason_category="risk",
                status="open",
                created_at=now - timedelta(days=40),
            ),
            McpScoreDispute(
                server_id="srv1",
                submitted_by="analyst-2",
                proposed_overall_risk=0.8,
                explanation="Recently opened dispute.",
                reason_category="risk",
                status="open",
                created_at=now - timedelta(days=2),
            ),
            McpScoreDispute(
                server_id="srv1",
                submitted_by="analyst-3",
                proposed_overall_risk=0.7,
                explanation="Resolved dispute.",
                reason_category="risk",
                status="resolved",
                created_at=now - timedelta(days=10),
                resolved_at=now - timedelta(days=3),
            ),
            McpScoreDispute(
                server_id="srv1",
                submitted_by="analyst-4",
                proposed_overall_risk=0.6,
                explanation="Rejected dispute.",
                reason_category="risk",
                status="rejected",
                created_at=now - timedelta(days=5),
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

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/disputes/ageing")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 4, body
    assert body["overdue"] == 1, body
    assert len(body["buckets"]) == 6, body
    assert all(bucket["count"] >= 0 for bucket in body["buckets"]), body
    assert sum(bucket["count"] for bucket in body["buckets"]) == 4, body
    assert all(
        isinstance(item["status"], str) and item["count"] >= 0
        for item in body["by_status"]
    ), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)