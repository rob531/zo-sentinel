"""HTTP surface for score dispute detail retrieval."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_score_dispute_detail

router = APIRouter(prefix="/api", tags=["score_dispute_detail"])


class ScoreDispute(BaseModel):
    id: int
    server_id: str
    submitted_by: str | None
    proposed_overall_risk: str | None
    proposed_axes: list
    reason_category: str | None
    explanation: str | None
    status: str
    admin_note: str | None
    created_at: str | None
    resolved_at: str | None


class AxisView(BaseModel):
    axis_name: str
    label: str | None
    p_top: float | None
    escalated: bool
    escalated_to: str | None


class ScoreDisputeDetail(BaseModel):
    dispute: ScoreDispute
    current_axes: list[AxisView]
    current_overall_risk: str | None


@router.get(
    "/disputes/{dispute_id}",
    response_model=ScoreDisputeDetail,
)
def score_dispute_detail(
    dispute_id: int,
    db: Session = Depends(get_session),
) -> ScoreDisputeDetail:
    detail = get_score_dispute_detail(db, dispute_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Score dispute not found")
    return detail


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore, McpScoreDispute

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestingSession() as session:
        session.add(
            McpScoreDispute(
                id=1,
                server_id="server-1",
                submitted_by="user-1",
                proposed_overall_risk="HIGH_RISK_ISOLATED",
                proposed_axes=[],
                reason_category="model_error",
                explanation="The proposed score appears incorrect.",
                status="pending",
            )
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="overall_risk",
                    label="HIGH_RISK_ISOLATED",
                    label_index=0,
                    probs={},
                    p_top=0.9,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="v1",
                    model_version="model-1",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-1",
                    axis_name="auth_strength",
                    label="STRONG",
                    label_index=0,
                    probs={},
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="v1",
                    model_version="model-1",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/1")
        assert response.status_code == 200
        body = response.json()
        assert body["dispute"]["id"] == 1
        assert body["dispute"]["proposed_overall_risk"] == "HIGH_RISK_ISOLATED"
        assert len(body["current_axes"]) == 2
        assert any(axis["axis_name"] == "overall_risk" for axis in body["current_axes"])
        assert client.get("/api/disputes/999999").status_code == 404

    print("PASS")