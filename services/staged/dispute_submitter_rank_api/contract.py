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
                server_id="srv-1",
                submitted_by="alice",
                proposed_overall_risk=0.8,
                reason_category="accuracy",
                status="resolved",
                created_at=now - timedelta(days=2),
                resolved_at=now - timedelta(days=1),
                explanation="Incorrect score",
            ),
            McpScoreDispute(
                id=2,
                server_id="srv-2",
                submitted_by="alice",
                proposed_overall_risk=0.7,
                reason_category="accuracy",
                status="resolved",
                created_at=now - timedelta(days=4),
                resolved_at=now - timedelta(days=3),
                explanation="Incorrect score",
            ),
            McpScoreDispute(
                id=3,
                server_id="srv-1",
                submitted_by="alice",
                proposed_overall_risk=0.9,
                reason_category="coverage",
                status="resolved",
                created_at=now - timedelta(days=6),
                resolved_at=now - timedelta(days=5),
                explanation="Incomplete coverage",
            ),
            McpScoreDispute(
                id=4,
                server_id="srv-3",
                submitted_by="bob",
                proposed_overall_risk=0.6,
                reason_category="accuracy",
                status="open",
                created_at=now - timedelta(days=3),
                explanation="Incorrect score",
            ),
            McpScoreDispute(
                id=5,
                server_id="srv-4",
                submitted_by="alice",
                proposed_overall_risk=0.5,
                reason_category="accuracy",
                status="resolved",
                created_at=now - timedelta(days=40),
                resolved_at=now - timedelta(days=39),
                explanation="Outside the requested window",
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
    response = client.get("/api/disputes/submitters?days=30")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == 30, body
    assert len(body["submitters"]) == 2, body

    submitters = {item["submitted_by"]: item for item in body["submitters"]}
    assert set(submitters) == {"alice", "bob"}, body

    alice = submitters["alice"]
    assert alice["disputes_submitted"] == 3, body
    assert alice["disputes_resolved"] == 3, body
    assert alice["resolution_rate"] == 1.0, body
    assert alice["distinct_servers"] == 2, body
    assert alice["top_reason_category"] == "accuracy", body

    bob = submitters["bob"]
    assert bob["disputes_submitted"] == 1, body
    assert bob["disputes_resolved"] == 0, body
    assert bob["resolution_rate"] == 0.0, body
    assert bob["distinct_servers"] == 1, body
    assert bob["top_reason_category"] == "accuracy", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)