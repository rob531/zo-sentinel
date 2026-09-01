from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import compute_risk_tier
from pydantic import BaseModel

router = APIRouter()

class RiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    criteria_version: str
    override_applied: bool
    axes_summary: List[Dict[str, float]]

class RiskTierRequest(BaseModel):
    server_id: str
    risk_tier: str

@router.get("/api/risk/tier/{server_id}", response_model=RiskTierResponse)
async def get_risk_tier(server_id: str, session: Session = Depends(get_session)):
    # Get axis scores from McpLlmAxisScore
    axis_scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    # Get current trust override from McpServerRegistry
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Prepare axis scores data for compute_risk_tier
    scores_data = [
        {
            "axis_name": score.axis_name,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "decision_rule_version": score.decision_rule_version
        }
        for score in axis_scores
    ]

    # Compute risk tier
    risk_tier = compute_risk_tier(server_id, scores_data)

    # Prepare response
    response = {
        "server_id": server_id,
        "risk_tier": risk_tier["risk_tier"],
        "criteria_version": risk_tier["criteria_version"],
        "override_applied": risk_tier["override_applied"],
        "axes_summary": [
            {
                "axis_name": score["axis_name"],
                "p_top": score["p_top"],
                "p_critical": score["p_critical"]
            }
            for score in scores_data
        ]
    }

    return response

@router.post("/api/risk/tier/update")
async def update_risk_tier(request: RiskTierRequest, session: Session = Depends(get_session)):
    # Update risk tier in McpServerRegistry
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == request.server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server.risk_tier = request.risk_tier
    session.commit()

    return {"status": "success", "server_id": request.server_id}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Test data
    test_server_id = "test_server_1"
    test_axis_scores = [
        {"axis_name": "overall_risk", "p_top": 80, "p_critical": 0.0, "decision_rule_version": "1.0"},
        {"axis_name": "auth_strength", "p_top": 75, "p_critical": 0.0, "decision_rule_version": "1.0"},
        {"axis_name": "capability_breadth", "p_top": 85, "p_critical": 0.0, "decision_rule_version": "1.0"},
        {"axis_name": "data_sensitivity", "p_top": 90, "p_critical": 0.0, "decision_rule_version": "1.0"},
        {"axis_name": "network_egress", "p_top": 70, "p_critical": 0.0, "decision_rule_version": "1.0"},
        {"axis_name": "maintainer_trust", "p_top": 95, "p_critical": 0.0, "decision_rule_version": "1.0"},
        {"axis_name": "exploit_surface", "p_top": 65, "p_critical": 0.0, "decision_rule_version": "1.0"}
    ]

    # Insert test data
    test_session = TestSession()
    test_server = McpServerRegistry(
        server_id=test_server_id,
        risk_tier="UNKNOWN",
        trust_override=False
    )
    test_session.add(test_server)

    for score in test_axis_scores:
        test_axis_score = McpLlmAxisScore(
            server_id=test_server_id,
            axis_name=score["axis_name"],
            p_top=score["p_top"],
            p_critical=score["p_critical"],
            decision_rule_version=score["decision_rule_version"]
        )
        test_session.add(test_axis_score)

    test_session.commit()

    # Test GET endpoint
    response = client.get(f"/api/risk/tier/{test_server_id}")
    assert response.status_code == 200
    assert response.json()["risk_tier"] == "TRUSTED_GENERAL"
    assert response.json()["criteria_version"] == "1.0"
    assert response.json()["override_applied"] is False

    # Test POST endpoint
    update_response = client.post(
        "/api/risk/tier/update",
        json={"server_id": test_server_id, "risk_tier": "TRUSTED_RESEARCH"}
    )
    assert update_response.status_code == 200

    # Verify update
    updated_response = client.get(f"/api/risk/tier/{test_server_id}")
    assert updated_response.status_code == 200
    assert updated_response.json()["risk_tier"] == "TRUSTED_RESEARCH"

    print("PASS")