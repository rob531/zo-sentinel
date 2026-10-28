from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_dispute_status_workflow

router = APIRouter(prefix="/api", tags=["disputes"])


@router.get("/disputes/status-workflow")
def dispute_status_workflow(
    days: int = 30,
    session: Session = Depends(get_session),
):
    return get_dispute_status_workflow(days, session)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    submitted_by INTEGER,
                    proposed_overall_risk REAL,
                    proposed_axes TEXT,
                    reason_category TEXT,
                    explanation TEXT,
                    status TEXT,
                    admin_note TEXT,
                    created_at DATETIME,
                    resolved_at DATETIME
                )
                """
            )
        )

    now = datetime.utcnow()
    with TestingSessionLocal() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    server_id,
                    submitted_by,
                    proposed_overall_risk,
                    proposed_axes,
                    reason_category,
                    explanation,
                    status,
                    admin_note,
                    created_at,
                    resolved_at
                ) VALUES (
                    :server_id,
                    :submitted_by,
                    :proposed_overall_risk,
                    :proposed_axes,
                    :reason_category,
                    :explanation,
                    :status,
                    :admin_note,
                    :created_at,
                    :resolved_at
                )
                """
            ),
            [
                {
                    "server_id": 1,
                    "submitted_by": 1,
                    "proposed_overall_risk": 0.8,
                    "proposed_axes": "{}",
                    "reason_category": "risk_score",
                    "explanation": "Risk score appears incorrect",
                    "status": "resolved",
                    "admin_note": "Corrected",
                    "created_at": now - timedelta(days=4),
                    "resolved_at": now - timedelta(days=2),
                },
                {
                    "server_id": 2,
                    "submitted_by": 1,
                    "proposed_overall_risk": 0.6,
                    "proposed_axes": "{}",
                    "reason_category": "axis_score",
                    "explanation": "Axis score appears incorrect",
                    "status": "open",
                    "admin_note": None,
                    "created_at": now - timedelta(days=1),
                    "resolved_at": None,
                },
                {
                    "server_id": 3,
                    "submitted_by": 1,
                    "proposed_overall_risk": 0.4,
                    "proposed_axes": "{}",
                    "reason_category": "other",
                    "explanation": "Invalid dispute",
                    "status": "rejected",
                    "admin_note": "Rejected",
                    "created_at": now - timedelta(hours=10),
                    "resolved_at": now - timedelta(hours=9),
                },
            ],
        )
        session.commit()
        assert session.query(McpScoreDispute).count() == 3

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/status-workflow", params={"days": 30})

    assert response.status_code == 200
    payload = response.json()
    assert sum(item["count"] for item in payload["by_status"]) == 3
    assert payload["open_disputes"] == 1
    assert payload["avg_resolution_hours"] is not None
    assert payload["avg_resolution_hours"] == 48.0
    print("PASS")