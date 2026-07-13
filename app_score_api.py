from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPLLMAxisScore
from app.dependency_overrides import dependency_overrides
from app import write_service
from app.models import MCPLLMAxisScore

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool

class ServerScoreResponse(BaseModel):
    server_id: str
    axes: List[AxisScore]
    overall_risk_score: float
    risk_tier: str
    criteria_version: str
    scored_at: datetime

def get_risk_tier(overall_risk: float, axes: List[AxisScore]) -> str:
    if any(axis.escalated for axis in axes):
        return "HIGH_RISK_ISOLATED"
    if overall_risk > 75:
        return "TRUSTED_GENERAL"
    if overall_risk > 60:
        return "TRUSTED_RESEARCH"
    if overall_risk > 45:
        return "ENTERPRISE_CONTROLLED"
    if overall_risk > 30:
        return "CAUTION_LIMITED"
    if overall_risk > 15:
        return "HIGH_RISK_ISOLATED"
    return "KNOWN_THREAT"

@router.get("/app/servers/{server_id}/score", response_model=ServerScoreResponse)
async def get_server_score(server_id: str, session=Depends(get_session)):
    # Query the database for the server's scores
    scores = session.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id).all()

    if not scores:
        raise HTTPException(status_code=404, detail="Server not found")

    # Extract the scores for each axis
    axes = []
    overall_risk = 0
    for score in scores:
        axes.append(AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            escalated=score.escalated
        ))
        if score.axis_name == "overall_risk":
            overall_risk = score.p_top

    # Determine the risk tier
    risk_tier = get_risk_tier(overall_risk, axes)

    # Return the response
    return ServerScoreResponse(
        server_id=server_id,
        axes=axes,
        overall_risk_score=overall_risk,
        risk_tier=risk_tier,
        criteria_version="1.0",
        scored_at=datetime.now()
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Create a test client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Add test data
    test_data = [
        MCPLLMAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            label="Overall Risk",
            p_top=80,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server1",
            axis_name="auth_strength",
            label="Auth Strength",
            p_top=70,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server1",
            axis_name="capability_breadth",
            label="Capability Breadth",
            p_top=60,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server1",
            axis_name="data_sensitivity",
            label="Data Sensitivity",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server1",
            axis_name="network_egress",
            label="Network Egress",
            p_top=40,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server1",
            axis_name="maintainer_trust",
            label="Maintainer Trust",
            p_top=30,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server1",
            axis_name="exploit_surface",
            label="Exploit Surface",
            p_top=20,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            label="Overall Risk",
            p_top=10,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server2",
            axis_name="auth_strength",
            label="Auth Strength",
            p_top=10,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server2",
            axis_name="capability_breadth",
            label="Capability Breadth",
            p_top=10,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server2",
            axis_name="data_sensitivity",
            label="Data Sensitivity",
            p_top=10,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server2",
            axis_name="network_egress",
            label="Network Egress",
            p_top=10,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server2",
            axis_name="maintainer_trust",
            label="Maintainer Trust",
            p_top=10,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server2",
            axis_name="exploit_surface",
            label="Exploit Surface",
            p_top=10,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server3",
            axis_name="overall_risk",
            label="Overall Risk",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server3",
            axis_name="auth_strength",
            label="Auth Strength",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server3",
            axis_name="capability_breadth",
            label="Capability Breadth",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server3",
            axis_name="data_sensitivity",
            label="Data Sensitivity",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server3",
            axis_name="network_egress",
            label="Network Egress",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server3",
            axis_name="maintainer_trust",
            label="Maintainer Trust",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=False
        ),
        MCPLLMAxisScore(
            server_id="server3",
            axis_name="exploit_surface",
            label="Exploit Surface",
            p_top=50,
            p_critical=0,
            p_danger=0,
            escalated=True
        )
    ]

    session = SessionLocal()
    session.add_all(test_data)
    session.commit()

    # Test the endpoints
    response1 = client.get("/app/servers/server1/score")
    assert response1.json()["risk_tier"] == "TRUSTED_GENERAL"

    response2 = client.get("/app/servers/server2/score")
    assert response2.json()["risk_tier"] == "KNOWN_THREAT"

    response3 = client.get("/app/servers/server3/score")
    assert response3.json()["risk_tier"] == "HIGH_RISK_ISOLATED"

    print("PASS")