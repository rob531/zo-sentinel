from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from pydantic import BaseModel

router = APIRouter()

class RiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    overall_risk: float
    axes: Dict[str, Dict[str, float]]

def calculate_risk_tier(overall_risk: float) -> str:
    if overall_risk >= 0.8:
        return "Critical"
    elif overall_risk >= 0.6:
        return "High"
    elif overall_risk >= 0.4:
        return "Medium"
    elif overall_risk >= 0.2:
        return "Low"
    else:
        return "Minimal"

def get_server_risk_tier(server_id: str, db: Session = Depends(get_session)) -> Dict:
    # Get server metadata
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores
    axis_scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    # Calculate overall risk
    overall_risk = sum(score.score for score in axis_scores) / len(axis_scores)

    # Prepare axis details
    axes = {}
    for score in axis_scores:
        axes[score.axis_name] = {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger
        }

    # Determine risk tier
    risk_tier = calculate_risk_tier(overall_risk)

    return {
        "server_id": server_id,
        "risk_tier": risk_tier,
        "overall_risk": round(overall_risk, 2),
        "axes": axes
    }

@router.get("/servers/{server_id}/risk_tier", response_model=RiskTierResponse)
async def read_server_risk_tier(server_id: str, db: Session = Depends(get_session)):
    return get_server_risk_tier(server_id, db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    client = TestClient(app)

    # Insert test data
    test_session = TestSessionLocal()
    test_server = MCPServerRegistry(
        server_id="test_server_1",
        name="Test Server",
        description="Test server for risk tier calculation"
    )
    test_session.add(test_server)

    test_axis_scores = [
        MCPLLMAxisScores(
            server_id="test_server_1",
            axis_name="security",
            label="Security Risk",
            score=0.7,
            p_top=0.8,
            p_critical=0.6,
            p_danger=0.4
        ),
        MCPLLMAxisScores(
            server_id="test_server_1",
            axis_name="privacy",
            label="Privacy Risk",
            score=0.5,
            p_top=0.7,
            p_critical=0.5,
            p_danger=0.3
        )
    ]
    test_session.add_all(test_axis_scores)
    test_session.commit()

    # Test endpoint
    response = client.get("/servers/test_server_1/risk_tier")
    assert response.status_code == 200
    assert response.json()["server_id"] == "test_server_1"
    assert response.json()["risk_tier"] == "High"
    assert response.json()["overall_risk"] == 0.6
    assert len(response.json()["axes"]) == 2

    print("PASS")