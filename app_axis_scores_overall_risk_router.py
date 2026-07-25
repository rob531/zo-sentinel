from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry
from sqlalchemy.orm import Session
import requests
from datetime import datetime

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: Dict[str, float]
    escalated: bool

class ServerRiskResponse(BaseModel):
    server_id: str
    axes: List[AxisScore]
    overall_risk: float
    computed_tier: str
    scored_at: datetime

def trust_gate(overall_risk: float) -> str:
    if overall_risk >= 0.9:
        return "red"
    elif overall_risk >= 0.7:
        return "orange"
    elif overall_risk >= 0.5:
        return "yellow"
    else:
        return "green"

@router.get("/servers/{server_id}/axis-scores", response_model=ServerRiskResponse)
async def get_server_axis_scores(server_id: str, session: Session = Depends(get_session)):
    # Get server from registry
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores
    axis_scores = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    # Prepare axes data
    axes = []
    for score in axis_scores:
        axes.append(AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            probs=score.probs,
            escalated=score.escalated
        ))

    # Get overall risk
    overall_risk = axis_scores[0].overall_risk  # Assuming all records have same overall_risk

    # Compute tier
    computed_tier = trust_gate(overall_risk)

    # Get scored_at
    scored_at = axis_scores[0].scored_at

    return ServerRiskResponse(
        server_id=server_id,
        axes=axes,
        overall_risk=overall_risk,
        computed_tier=computed_tier,
        scored_at=scored_at
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override get_session for testing
    dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test app
    app = FastAPI()
    app.include_router(router)

    # Seed test data
    with TestSessionLocal() as session:
        # Add test server
        test_server = MCPServerRegistry(
            server_id="test-server-1",
            name="Test Server",
            description="Test server for axis scores",
            org_id="test-org-1"
        )
        session.add(test_server)

        # Add test axis scores
        test_scores = MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="auth_strength",
            label="Authentication Strength",
            p_top=0.8,
            p_critical=0.6,
            p_danger=0.4,
            probs={"high": 0.8, "medium": 0.2, "low": 0.0},
            escalated=False,
            overall_risk=0.75,
            scored_at=datetime.now()
        )
        session.add(test_scores)
        session.commit()

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/servers/test-server-1/axis-scores")
    assert response.status_code == 200
    data = response.json()

    # Verify response shape
    assert "server_id" in data
    assert "axes" in data
    assert "overall_risk" in data
    assert "computed_tier" in data
    assert "scored_at" in data

    # Verify computed tier
    assert data["computed_tier"] == "orange"  # 0.75 should be orange

    print("PASS")