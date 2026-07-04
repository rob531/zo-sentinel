from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPRiskRegister, MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes

router = APIRouter()

class RiskTierDetail(BaseModel):
    server_id: int
    server_name: str
    critical_axis_override: Optional[bool]
    axis_scores: Dict[str, float]
    disputes: List[Dict[str, str]]

class RiskTierAnalysisResponse(BaseModel):
    tier: str
    servers: Dict[int, RiskTierDetail]

def calculate_risk_tier(score: float, critical_axis_override: bool = False) -> str:
    if critical_axis_override:
        return "CRITICAL"
    if score >= 0.9:
        return "EXTREME"
    elif score >= 0.7:
        return "HIGH"
    elif score >= 0.5:
        return "MEDIUM"
    elif score >= 0.3:
        return "LOW"
    elif score >= 0.1:
        return "MINIMAL"
    else:
        return "NEGLIGIBLE"

def get_server_details(session: Session, server_id: int) -> Optional[RiskTierDetail]:
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        return None

    critical_axis_override = session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_id == server_id,
        MCPScoreDisputes.axis == "CRITICAL"
    ).first() is not None

    axis_scores = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()

    scores_dict = {axis.axis: axis.score for axis in axis_scores}

    disputes = session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_id == server_id
    ).all()

    disputes_list = [{
        "axis": dispute.axis,
        "comment": dispute.comment
    } for dispute in disputes]

    return RiskTierDetail(
        server_id=server.id,
        server_name=server.name,
        critical_axis_override=critical_axis_override,
        axis_scores=scores_dict,
        disputes=disputes_list
    )

@router.get("/risk-tier-detail-analysis", response_model=Dict[str, RiskTierAnalysisResponse])
async def get_risk_tier_detail_analysis(session: Session = Depends(get_session)) -> Dict[str, RiskTierAnalysisResponse]:
    risk_registers = session.query(MCPRiskRegister).all()

    response = {
        "EXTREME": RiskTierAnalysisResponse(tier="EXTREME", servers={}),
        "HIGH": RiskTierAnalysisResponse(tier="HIGH", servers={}),
        "MEDIUM": RiskTierAnalysisResponse(tier="MEDIUM", servers={}),
        "LOW": RiskTierAnalysisResponse(tier="LOW", servers={}),
        "MINIMAL": RiskTierAnalysisResponse(tier="MINIMAL", servers={}),
        "NEGLIGIBLE": RiskTierAnalysisResponse(tier="NEGLIGIBLE", servers={}),
        "CRITICAL": RiskTierAnalysisResponse(tier="CRITICAL", servers={})
    }

    for register in risk_registers:
        server_details = get_server_details(session, register.server_id)
        if not server_details:
            continue

        tier = calculate_risk_tier(register.overall_score, server_details.critical_axis_override)
        response[tier].servers[register.server_id] = server_details

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(id=1, name="Test Server")
    test_session.add(test_server)

    test_llm_scores = [
        MCPLLMAxisScores(server_id=1, axis="SECURITY", score=0.8),
        MCPLLMAxisScores(server_id=1, axis="PERFORMANCE", score=0.6),
        MCPLLMAxisScores(server_id=1, axis="CRITICAL", score=0.9)
    ]
    test_session.add_all(test_llm_scores)

    test_dispute = MCPScoreDisputes(server_id=1, axis="CRITICAL", comment="Critical override")
    test_session.add(test_dispute)

    test_risk_register = MCPRiskRegister(server_id=1, overall_score=0.75)
    test_session.add(test_risk_register)

    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/risk-tier-detail-analysis")
    assert response.status_code == 200
    data = response.json()

    # Verify all tiers are present
    tiers = ["EXTREME", "HIGH", "MEDIUM", "LOW", "MINIMAL", "NEGLIGIBLE", "CRITICAL"]
    for tier in tiers:
        assert tier in data, f"Missing tier: {tier}"

    # Verify the override tier has the test server
    assert "CRITICAL" in data
    assert 1 in data["CRITICAL"]["servers"]

    print("PASS")