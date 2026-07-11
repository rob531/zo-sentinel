from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session

router = APIRouter()

class OverallRisk(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class ScoringMetadataResponse(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: str
    scored_at: datetime
    overall_risk: OverallRisk

@router.get("/servers/{server_id}/scoring-metadata", response_model=ScoringMetadataResponse)
def get_scoring_metadata(server_id: str, db: Session = Depends(get_session)):
    # Get the server from the registry
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get the latest scoring metadata
    latest_score = (
        db.query(MCPLLMAxisScores)
        .filter(MCPLLMAxisScores.server_id == server_id)
        .order_by(MCPLLMAxisScores.scored_at.desc())
        .first()
    )

    if not latest_score:
        raise HTTPException(status_code=404, detail="No scoring metadata found for this server")

    # Construct the response
    response = ScoringMetadataResponse(
        model_version=latest_score.model_version,
        decision_rule_version=latest_score.decision_rule_version,
        adapter_sha256=latest_score.adapter_sha256,
        scored_at=latest_score.scored_at,
        overall_risk=OverallRisk(
            label=latest_score.overall_risk_label,
            p_top=latest_score.overall_risk_p_top,
            p_critical=latest_score.overall_risk_p_critical,
            p_danger=latest_score.overall_risk_p_danger,
        )
    )

    return response

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(id="srv1", name="Test Server 1"),
        MCPServerRegistry(id="srv2", name="Test Server 2"),
        MCPServerRegistry(id="srv3", name="Test Server 3"),
        MCPLLMAxisScores(
            server_id="srv1",
            model_version="v1.0",
            decision_rule_version="v2.0",
            adapter_sha256="abc123",
            scored_at=datetime.now(),
            overall_risk_label="low",
            overall_risk_p_top=0.1,
            overall_risk_p_critical=0.2,
            overall_risk_p_danger=0.3,
        ),
    ])
    test_session.commit()

    # Run test
    client = TestClient(app)
    response = client.get("/servers/srv1/scoring-metadata")
    assert response.status_code == 200
    data = response.json()
    assert data["model_version"] is not None
    assert data["decision_rule_version"] is not None
    assert data["adapter_sha256"] is not None
    assert data["overall_risk"] is not None
    print("PASS")