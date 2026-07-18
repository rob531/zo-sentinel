from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry
from sqlalchemy.orm import Session
import requests

router = APIRouter()

@router.get("/servers/{server_id}/overall-risk")
async def get_overall_risk(server_id: str, db: Session = Depends(get_session)) -> dict:
    # Query overall risk score from mcp_llm_axis_scores
    axis_score = db.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis_name == 'overall_risk'
    ).first()

    if not axis_score:
        raise HTTPException(status_code=404, detail="Server or overall risk score not found")

    # Query risk tier from mcp_server_registry
    server = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    return {
        "server_id": server_id,
        "overall_risk_score": axis_score.p_top,
        "risk_tier": server.risk_tier
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Insert test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test_server",
        risk_tier="high"
    )
    test_axis_score = MCPLLMAxisScores(
        server_id="test_server",
        axis_name="overall_risk",
        p_top=0.85
    )
    test_session.add(test_server)
    test_session.add(test_axis_score)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test_server/overall-risk")
    assert response.status_code == 200
    assert response.json() == {
        "server_id": "test_server",
        "overall_risk_score": 0.85,
        "risk_tier": "high"
    }
    print("PASS")