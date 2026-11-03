"""Acceptance self-test for the score-dispute rollup service."""
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
    session = testing_session()
    try:
        session.add_all(
            [
                McpScoreDispute(
                    server_id="srv-1",
                    submitted_by="analyst-1",
                    proposed_overall_risk=0.8,
                    proposed_axes={"security": 0.9},
                    reason_category="false_positive",
                    explanation="Test dispute one",
                    status="open",
                    created_at=now - timedelta(hours=4),
                ),
                McpScoreDispute(
                    server_id="srv-2",
                    submitted_by="analyst-2",
                    proposed_overall_risk=0.6,
                    proposed_axes={"security": 0.7},
                    reason_category="scoring",
                    explanation="Test dispute two",
                    status="open",
                    created_at=now - timedelta(hours=2),
                ),
                McpScoreDispute(
                    server_id="srv-3",
                    submitted_by="analyst-3",
                    proposed_overall_risk=0.5,
                    proposed_axes={"security": 0.5},
                    reason_category="scoring",
                    explanation="Test dispute three",
                    status="open",
                    created_at=now - timedelta(hours=1),
                ),
                McpScoreDispute(
                    server_id="srv-4",
                    submitted_by="analyst-4",
                    proposed_overall_risk=0.4,
                    proposed_axes={"security": 0.4},
                    reason_category="false_positive",
                    explanation="Test dispute four",
                    status="resolved",
                    created_at=now - timedelta(days=1),
                    resolved_at=now,
                ),
                McpScoreDispute(
                    server_id="srv-5",
                    submitted_by="analyst-5",
                    proposed_overall_risk=0.3,
                    proposed_axes={"security": 0.3},
                    reason_category="other",
                    explanation="Test dispute five",
                    status="rejected",
                    created_at=now - timedelta(days=2),
                    resolved_at=now,
                ),
            ]
        )
        session.commit()
    finally:
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

    client = TestClient(app)
    response = client.get("/api/disputes/rollup")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 5, body
    assert body["filtered_total"] is None, body
    assert next(item["count"] for item in body["by_status"] if item["status"] == "open") == 3, body
    assert sum(item["count"] for item in body["by_reason_category"]) == 5, body
    assert body["oldest_open_created_at"] is not None, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)