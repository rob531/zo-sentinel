from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute
from services.staged.dispute_status_funnel_api.router import router


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    rows = [
        McpScoreDispute(
            server_id=101,
            submitted_by=201,
            proposed_overall_risk=0.90,
            proposed_axes={"authorization": 0.95},
            reason_category="Authorization",
            explanation="Open authorization dispute",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=1),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=102,
            submitted_by=202,
            proposed_overall_risk=0.80,
            proposed_axes={"data_handling": 0.85},
            reason_category="Authorization",
            explanation="Open data handling dispute",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=5),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=103,
            submitted_by=203,
            proposed_overall_risk=0.70,
            proposed_axes={"privacy": 0.75},
            reason_category="Privacy",
            explanation="Open privacy dispute",
            status="open",
            admin_note=None,
            created_at=now - timedelta(days=40),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=104,
            submitted_by=204,
            proposed_overall_risk=0.60,
            proposed_axes={"integrity": 0.65},
            reason_category="Integrity",
            explanation="Approved integrity dispute",
            status="approved",
            admin_note="Approved after review",
            created_at=now - timedelta(days=2),
            resolved_at=now - timedelta(days=2) + timedelta(hours=2),
        ),
        McpScoreDispute(
            server_id=105,
            submitted_by=205,
            proposed_overall_risk=0.50,
            proposed_axes={"availability": 0.55},
            reason_category="Availability",
            explanation="Approved availability dispute",
            status="approved",
            admin_note="Approved after review",
            created_at=now - timedelta(days=3),
            resolved_at=None,
        ),
        McpScoreDispute(
            server_id=106,
            submitted_by=206,
            proposed_overall_risk=0.40,
            proposed_axes={"supply_chain": 0.45},
            reason_category="Supply Chain",
            explanation="Rejected supply chain dispute",
            status="rejected",
            admin_note="Rejected after review",
            created_at=now - timedelta(days=4),
            resolved_at=now - timedelta(days=4) + timedelta(hours=8),
        ),
    ]

    with session_factory() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/disputes/status-funnel")
        assert response.status_code == 200, response.text
        payload = response.json()

    status_counts = {item["status"]: item["count"] for item in payload["status"]}
    assert status_counts["open"] == 3
    assert status_counts["approved"] == 2
    assert status_counts["rejected"] == 1

    assert payload["aging"]
    assert sum(item["count"] for item in payload["aging"]) == 3

    assert payload["resolution"]["resolved"] == 2
    assert abs(float(payload["resolution"]["median_hours"]) - 5.0) < 0.01

    reason_counts = {
        item["reason_category"]: item["count"] for item in payload["reason_categories"]
    }
    assert reason_counts["Authorization"] == 2
    assert len(payload["reason_categories"]) <= 5

    print("PASS")


if __name__ == "__main__":
    run()