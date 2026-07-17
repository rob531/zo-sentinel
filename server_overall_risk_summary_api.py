from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class OverallRiskResponse(BaseModel):
    overall_risk: float
    risk_tier: str
    server_name: str
    last_assessed: str

def get_risk_tier(score: float) -> str:
    if score >= 0.8:
        return "High"
    elif score >= 0.5:
        return "Medium"
    elif score >= 0.2:
        return "Low"
    else:
        return "Minimal"

def get_overall_risk(server_id: str, db: Session = Depends(get_session)) -> dict:
    # Query the overall risk score from mcp_llm_axis_scores
    axis_score = db.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis_name == 'overall_risk'
    ).first()

    if not axis_score:
        raise HTTPException(status_code=404, detail="Overall risk score not found")

    # Query server metadata from mcp_server_registry
    server = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Determine risk tier
    risk_tier = get_risk_tier(axis_score.score)

    # Format last assessed timestamp
    last_assessed = axis_score.timestamp.isoformat()

    return {
        "overall_risk": float(axis_score.score),
        "risk_tier": risk_tier,
        "server_name": server.server_name,
        "last_assessed": last_assessed
    }

@router.get("/servers/{server_id}/overall_risk", response_model=OverallRiskResponse)
async def overall_risk_endpoint(server_id: str, db: Session = Depends(get_session)):
    return get_overall_risk(server_id, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test app
    app = FastAPI()
    app.include_router(router)

    # Create test client
    client = TestClient(app)

    # Create test data
    test_server = MCPServerRegistry(
        server_id="test-server-1",
        server_name="Test Server 1",
        org_id="test-org-1"
    )

    test_score = MCPLLMAxisScores(
        server_id="test-server-1",
        axis_name="overall_risk",
        score=0.75,
        timestamp=datetime.now()
    )

    # Insert test data
    with SessionLocal() as db:
        db.add(test_server)
        db.add(test_score)
        db.commit()

    # Test the endpoint
    response = client.get("/servers/test-server-1/overall_risk")
    assert response.status_code == 200
    data = response.json()
    assert "overall_risk" in data
    assert "risk_tier" in data
    assert "server_name" in data
    assert "last_assessed" in data
    print("PASS")