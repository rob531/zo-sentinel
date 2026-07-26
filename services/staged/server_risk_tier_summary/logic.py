from fastapi import Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class ServerRiskTierSummary(BaseModel):
    server_id: str
    overall_risk: float
    risk_tier: str
    axis_scores: Dict[str, AxisScore]

def get_server_risk_tier_summary(server_id: str, db: Session = Depends(get_session)) -> ServerRiskTierSummary:
    # Get server from registry
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get all axis scores for this server
    axis_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    # Calculate overall risk (average of all axis scores)
    overall_risk = sum(score.score for score in axis_scores) / len(axis_scores)

    # Determine risk tier based on overall risk and critical axes
    risk_tier = "LOW"
    critical_axes = [score for score in axis_scores if score.axis_name == "critical"]

    if critical_axes and any(score.score >= 0.7 for score in critical_axes):
        risk_tier = "CRITICAL"
    elif overall_risk >= 0.7:
        risk_tier = "HIGH"
    elif overall_risk >= 0.5:
        risk_tier = "MEDIUM"

    # Prepare axis scores dictionary
    axis_scores_dict = {
        score.axis_name: AxisScore(
            label=score.axis_name,
            p_top=score.score,
            p_critical=score.score if score.axis_name == "critical" else 0.0,
            p_danger=score.score if score.axis_name == "danger" else 0.0
        )
        for score in axis_scores
    }

    return ServerRiskTierSummary(
        server_id=server_id,
        overall_risk=overall_risk,
        risk_tier=risk_tier,
        axis_scores=axis_scores_dict
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Add test endpoint
    @app.get("/api/server/{server_id}/risk_tier_summary")
    async def test_endpoint(server_id: str, db: Session = Depends(get_session)):
        return get_server_risk_tier_summary(server_id, db)

    # Insert test data
    with SessionLocal() as db:
        # Add test server
        test_server = McpServerRegistry(
            server_id="test_server_1",
            name="Test Server 1",
            description="Test server for risk tier summary"
        )
        db.add(test_server)

        # Add test axis scores
        test_scores = [
            McpLlmAxisScore(
                server_id="test_server_1",
                axis_name="overall_risk",
                score=0.6
            ),
            McpLlmAxisScore(
                server_id="test_server_1",
                axis_name="critical",
                score=0.5
            ),
            McpLlmAxisScore(
                server_id="test_server_1",
                axis_name="danger",
                score=0.4
            )
        ]
        db.add_all(test_scores)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/server/test_server_1/risk_tier_summary")
    assert response.status_code == 200
    data = response.json()

    # Verify overall_risk is correct average
    expected_overall = (0.6 + 0.5 + 0.4) / 3
    assert abs(data["overall_risk"] - expected_overall) < 0.001

    # Verify risk tier is correct (MEDIUM in this case)
    assert data["risk_tier"] == "MEDIUM"

    print("PASS")