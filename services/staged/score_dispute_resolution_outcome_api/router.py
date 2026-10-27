from typing import Any, Dict, List
from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_dispute_resolution_outcomes

router = APIRouter(prefix="/api", tags=["score-dispute-resolution-outcomes"])


class ScoreDisputeResolutionOutcomeResponse(BaseModel):
    days: int
    categories: List[Dict[str, Any]]
    total: int


@router.get(
    "/disputes/resolution-outcomes",
    response_model=ScoreDisputeResolutionOutcomeResponse,
)
def resolution_outcomes(
    days: int = 30,
    session: Session = Depends(get_session),
) -> ScoreDisputeResolutionOutcomeResponse:
    result = get_dispute_resolution_outcomes(session, days)
    return ScoreDisputeResolutionOutcomeResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        ("alpha", "RESOLVED", 2),
        ("alpha", "RESOLVED", 5),
        ("beta", "REJECTED", 2),
        ("beta", "REJECTED", 5),
    ]

    with TestSession() as session:
        for index, (category, status, elapsed_days) in enumerate(rows, start=1):
            created_at = now - timedelta(days=7)
            resolved_at = created_at + timedelta(days=elapsed_days)
            session.execute(
                text(
                    """
                    INSERT INTO mcp_score_disputes (
                        id,
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
                        :id,
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
                {
                    "id": index,
                    "server_id": 1,
                    "submitted_by": 1,
                    "proposed_overall_risk": "HIGH",
                    "proposed_axes": json.dumps({"overall": "HIGH"}),
                    "reason_category": category,
                    "explanation": "Contract seed row",
                    "status": status,
                    "admin_note": None,
                    "created_at": created_at,
                    "resolved_at": resolved_at,
                },
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/resolution-outcomes?days=30")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4, payload
        assert any(
            category["dispute_count"] == 2
            and abs(category["mean_resolution_days"] - 3.5) < 0.001
            for category in payload["categories"]
        ), payload

    print("PASS")