# deps: fastapi, pydantic, sqlalchemy, sqlmodel, passlib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User, ApiKey

router = APIRouter()

class RiskTierDeriverScoringConsumerRequest(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: str
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    scored_at: str

class RiskTierDeriverScoringConsumerResponse(BaseModel):
    message: str

@router.post("/risk-tier-deriver-scoring-consumer", response_model=RiskTierDeriverScoringConsumerResponse)
async def risk_tier_deriver_scoring_consumer(
    request: RiskTierDeriverScoringConsumerRequest,
    db: Session = Depends(get_session),
):
    try:
        server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == request.server_id).first()
        if not server:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

        axis_score = McpLlmAxisScore(
            server_id=request.server_id,
            axis_name=request.axis_name,
            label=request.label,
            label_index=request.label_index,
            probs=request.probs,
            p_top=request.p_top,
            p_critical=request.p_critical,
            p_danger=request.p_danger,
            escalated=request.escalated,
            escalated_to=request.escalated_to,
            decision_rule_version=request.decision_rule_version,
            model_version=request.model_version,
            adapter_sha256=request.adapter_sha256,
            scored_at=request.scored_at,
        )

        db.add(axis_score)
        db.commit()
        return {"message": "Risk tier deriver scoring consumer processed successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    test_request = {
        "server_id": "test_server",
        "axis_name": "test_axis",
        "label": "test_label",
        "label_index": 1,
        "probs": {"test_prob": 0.5},
        "p_top": 0.5,
        "p_critical": 0.5,
        "p_danger": 0.5,
        "escalated": False,
        "escalated_to": "",
        "decision_rule_version": "1.0",
        "model_version": "1.0",
        "adapter_sha256": "test_hash",
        "scored_at": "2023-01-01T00:00:00"
    }

    response = client.post("/risk-tier-deriver-scoring-consumer", json=test_request)
    assert response.status_code == 200
    assert response.json() == {"message": "Risk tier deriver scoring consumer processed successfully"}

    print("PASS")