from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func
import requests

router = APIRouter()

def get_server_risk_summary(server_id: str, db: Session = Depends(get_session)) -> Dict[str, Any]:
    # Get server registry data
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores
    axis_scores = db.query(
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.p_top,
        MCPLLMAxisScores.p_critical,
        MCPLLMAxisScores.p_danger,
        MCPLLMAxisScores.scored_at
    ).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()

    # Process axis scores into a dictionary
    axes = {
        axis.axis_name: {
            "p_top": axis.p_top,
            "p_critical": axis.p_critical,
            "p_danger": axis.p_danger
        }
        for axis in axis_scores
    }

    # Determine the latest assessment time
    latest_assessment = max([axis.scored_at for axis in axis_scores], default=datetime.min)

    # Calculate overall risk (average of all p_danger values)
    overall_risk = sum(axis.p_danger for axis in axis_scores) / len(axis_scores) if axis_scores else 0.0

    return {
        "overall_risk": overall_risk,
        "axes": axes,
        "risk_tier": server.risk_tier,
        "verdict": server.verdict,
        "last_assessed": latest_assessment.isoformat()
    }

@router.get("/servers/{server_id}/risk_summary")
async def risk_summary(server_id: str, db: Session = Depends(get_session)):
    return get_server_risk_summary(server_id, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Create a test session
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Create test tables
    Base.metadata.create_all(engine)

    # Add test data
    test_server = MCPServerRegistry(
        server_id="test_server_1",
        trust_score=0.85,
        verdict="approved",
        risk_tier="low"
    )
    test_session.add(test_server)

    test_axis_1 = MCPLLMAxisScores(
        server_id="test_server_1",
        axis_name="security",
        p_top=0.9,
        p_critical=0.8,
        p_danger=0.7,
        decision_rule_version="1.0",
        model_version="1.0",
        scored_at=datetime.now()
    )
    test_session.add(test_axis_1)

    test_axis_2 = MCPLLMAxisScores(
        server_id="test_server_1",
        axis_name="privacy",
        p_top=0.85,
        p_critical=0.75,
        p_danger=0.65,
        decision_rule_version="1.0",
        model_version="1.0",
        scored_at=datetime.now()
    )
    test_session.add(test_axis_2)

    test_session.commit()

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(router)
    response = client.get("/servers/test_server_1/risk_summary")
    assert response.status_code == 200
    result = response.json()

    # Verify the result structure
    assert "overall_risk" in result
    assert "axes" in result
    assert "risk_tier" in result
    assert "verdict" in result
    assert "last_assessed" in result

    print("PASS")