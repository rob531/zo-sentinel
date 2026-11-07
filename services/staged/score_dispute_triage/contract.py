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
    from app.models import Base, McpScoreDispute, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    session = testing_session()
    try:
        session.add(
            McpServerRegistry(
                server_id="srv-dispute-1",
                name="Disputed Server",
                risk_tier="HIGH",
            )
        )
        session.add(
            McpScoreDispute(
                id=1,
                server_id="srv-dispute-1",
                submitted_by="reviewer@example.com",
                proposed_overall_risk="HIGH",
                proposed_axes={"overall": 0.95},
                reason_category="model_disagreement",
                explanation="The current assessment and the submitted assessment disagree.",
                status="open",
                created_at=now - timedelta(days=3),
            )
        )
        session.add(
            McpScoreDispute(
                id=2,
                server_id="srv-dispute-1",
                submitted_by="reviewer@example.com",
                proposed_overall_risk="LOW",
                proposed_axes={"overall": 0.1},
                reason_category="model_disagreement",
                explanation="This dispute has already been resolved.",
                status="resolved",
                created_at=now - timedelta(days=5),
                resolved_at=now,
            )
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
    response = client.get("/api/disputes/triage", params={"status": "open", "limit": 50})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1, body
    assert len(body["disputes"]) == 1, body

    dispute = body["disputes"][0]
    assert dispute["id"] == 1, body
    assert dispute["server_id"] == "srv-dispute-1", body
    assert dispute["server_name"] == "Disputed Server", body
    assert dispute["current_risk_tier"] == "HIGH", body
    assert dispute["status"] == "open", body
    assert dispute["age_days"] >= 0, body

    assert all(item["id"] != 2 for item in body["disputes"]), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)