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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    s = TestingSession()
    s.add_all(
        [
            McpScoreDispute(
                server_id="srv-pending-1",
                submitted_by="alice",
                proposed_overall_risk="HIGH",
                reason_category="access",
                status="pending",
                created_at=now - timedelta(days=10),
                resolved_at=None,
                explanation="Pending access review",
            ),
            McpScoreDispute(
                server_id="srv-pending-2",
                submitted_by="bob",
                proposed_overall_risk="MEDIUM",
                reason_category="access",
                status="pending",
                created_at=now - timedelta(days=5),
                resolved_at=None,
                explanation="Pending access review",
            ),
            McpScoreDispute(
                server_id="srv-approved",
                submitted_by="carol",
                proposed_overall_risk="LOW",
                reason_category="policy",
                status="approved",
                created_at=now - timedelta(days=20),
                resolved_at=now - timedelta(days=1),
                explanation="Approved policy change",
            ),
            McpScoreDispute(
                server_id="srv-rejected-open",
                submitted_by="dave",
                proposed_overall_risk="CRITICAL",
                reason_category="policy",
                status="rejected",
                created_at=now - timedelta(days=2),
                resolved_at=None,
                explanation="Rejected policy change",
            ),
            McpScoreDispute(
                server_id="srv-rejected-resolved",
                submitted_by="erin",
                proposed_overall_risk="MEDIUM",
                reason_category="policy",
                status="rejected",
                created_at=now - timedelta(days=30),
                resolved_at=now - timedelta(days=10),
                explanation="Previously rejected policy change",
            ),
        ]
    )
    s.commit()
    s.close()

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
    response = client.get("/api/disputes/status-breakdown")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 5, body
    status_counts = {row["status"]: row["count"] for row in body["by_status"]}
    assert status_counts.get("pending") == 2, body
    assert status_counts.get("approved") == 1, body
    assert status_counts.get("rejected") == 2, body

    reason_counts = {
        row["reason_category"]: row for row in body["by_reason"]
    }
    assert reason_counts["access"]["count"] == 2, body
    assert reason_counts["access"]["open"] == 2, body
    assert reason_counts["policy"]["count"] == 3, body
    assert reason_counts["policy"]["open"] == 1, body

    oldest_ids = {row["id"] for row in body["oldest_open"]}
    seeded_open_ids = {
        1,
        2,
        4,
    }
    assert oldest_ids == seeded_open_ids, body
    assert all(
        row["status"] in {"pending", "rejected"}
        for row in body["oldest_open"]
    ), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)