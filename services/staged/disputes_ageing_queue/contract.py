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

    now = datetime.utcnow()
    session = TestingSession()
    session.add(McpServerRegistry(server_id="srv-a", name="Server A"))
    session.add(McpServerRegistry(server_id="srv-b", name="Server B"))
    session.add(
        McpScoreDispute(
            id=101,
            server_id="missing-server",
            submitted_by="admin-a",
            proposed_overall_risk="HIGH",
            explanation="Unknown-server dispute",
            reason_category="score_mismatch",
            status="open",
            created_at=now - timedelta(hours=12),
            resolved_at=None,
        )
    )
    session.add(
        McpScoreDispute(
            id=102,
            server_id="srv-a",
            submitted_by="admin-b",
            proposed_overall_risk="LOW",
            explanation="Ten-day-old dispute",
            reason_category="tier_change",
            status="open",
            created_at=now - timedelta(days=10),
            resolved_at=None,
        )
    )
    session.add(
        McpScoreDispute(
            id=103,
            server_id="srv-a",
            submitted_by="admin-a",
            proposed_overall_risk="MEDIUM",
            explanation="Resolved dispute",
            reason_category="other",
            status="resolved",
            created_at=now - timedelta(days=5),
            resolved_at=now - timedelta(days=4),
        )
    )
    session.add(
        McpScoreDispute(
            id=104,
            server_id="srv-b",
            submitted_by="admin-b",
            proposed_overall_risk="HIGH",
            explanation="Already handled dispute",
            reason_category="manual_review",
            status="resolved",
            created_at=now - timedelta(days=2),
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

    with TestClient(app) as client:
        response = client.get("/api/disputes/ageing")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["total_open"] == 2, body
    buckets = {item["bucket"]: item["count"] for item in body["buckets"]}
    assert buckets.get("0-1d") == 1, body
    assert buckets.get("7-30d") == 1, body
    assert buckets.get("1-3d", 0) == 0, body
    assert buckets.get("3-7d", 0) == 0, body
    assert buckets.get("30d+", 0) == 0, body

    oldest = body["oldest"]
    oldest_by_id = {item["id"]: item for item in oldest}
    assert 102 in oldest_by_id, body
    assert 101 in oldest_by_id, body
    assert 103 not in oldest_by_id, body
    assert 104 not in oldest_by_id, body
    assert oldest_by_id[101]["server_name"] is None, body
    assert oldest_by_id[102]["server_name"] == "Server A", body
    assert oldest_by_id[102]["age_days"] >= 9.9, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)