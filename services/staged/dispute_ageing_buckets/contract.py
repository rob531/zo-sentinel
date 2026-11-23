"""Acceptance self-test for the dispute ageing buckets service."""
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

    now = datetime.utcnow()
    session = TestingSession()
    session.add(
        McpScoreDispute(
            id=1,
            server_id="srv1",
            status="OPEN",
            reason_category="SCORING",
            proposed_overall_risk=0.5,
            explanation="A recent open dispute",
            submitted_by="tester",
            created_at=now - timedelta(days=2),
        )
    )
    session.add(
        McpScoreDispute(
            id=2,
            server_id="srv2",
            status="OPEN",
            reason_category="SCORING",
            proposed_overall_risk=0.7,
            explanation="An older open dispute",
            submitted_by="tester",
            created_at=now - timedelta(days=40),
        )
    )
    session.add(
        McpScoreDispute(
            id=3,
            server_id="srv3",
            status="RESOLVED",
            reason_category="SCORING",
            proposed_overall_risk=0.2,
            explanation="A resolved dispute",
            submitted_by="tester",
            created_at=now - timedelta(days=10),
            resolved_at=now - timedelta(days=1),
        )
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
    response = client.get("/api/disputes/ageing-buckets")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_open"] == 2, body
    buckets = {item["bucket"]: item["count"] for item in body["buckets"]}
    assert buckets["31-90"] == 1, body
    assert body["by_status"]["OPEN"] == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)