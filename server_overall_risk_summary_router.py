from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScores
from sqlalchemy.orm import Session
import math

router = APIRouter()

def calculate_overall_risk(scores: List[float]) -> float:
    """Calculate overall risk score from axis scores."""
    return math.sqrt(sum(score ** 2 for score in scores) / len(scores))

def determine_risk_tier(overall_risk: float) -> str:
    """Determine risk tier based on overall risk score."""
    if overall_risk < 0.2:
        return "Low"
    elif overall_risk < 0.5:
        return "Medium"
    elif overall_risk < 0.8:
        return "High"
    else:
        return "Critical"

def get_axis_breakdown(scores: List[MCPLLMAxisScores]) -> Dict[str, Dict[str, float]]:
    """Get breakdown of each axis with its label and top probability."""
    return {
        score.axis_name: {
            "label": score.label,
            "p_top": score.p_top
        }
        for score in scores
    }

@router.get("/servers/{server_id}/overall-risk-summary")
async def get_overall_risk_summary(server_id: str, db: Session = Depends(get_session)) -> Dict:
    """Get overall risk summary for a server."""
    scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    if not scores:
        raise HTTPException(status_code=404, detail="Server not found")

    if len(scores) != 7:
        raise HTTPException(status_code=500, detail="Incomplete axis scores")

    overall_risk = calculate_overall_risk([score.score for score in scores])
    risk_tier = determine_risk_tier(overall_risk)
    axes = get_axis_breakdown(scores)

    return {
        "server_id": server_id,
        "overall_risk": overall_risk,
        "risk_tier": risk_tier,
        "axes": axes
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_server_id = "test_server_123"
    test_scores = [
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis1",
            score=0.1,
            label="label1",
            p_top=0.9
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis2",
            score=0.2,
            label="label2",
            p_top=0.8
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis3",
            score=0.3,
            label="label3",
            p_top=0.7
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis4",
            score=0.4,
            label="label4",
            p_top=0.6
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis5",
            score=0.5,
            label="label5",
            p_top=0.5
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis6",
            score=0.6,
            label="label6",
            p_top=0.4
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis7",
            score=0.7,
            label="label7",
            p_top=0.3
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/servers/{test_server_id}/overall-risk-summary")
    assert response.status_code == 200
    result = response.json()
    assert isinstance(result["overall_risk"], float)
    assert isinstance(result["risk_tier"], str)
    assert isinstance(result["axes"], dict)
    print("PASS")