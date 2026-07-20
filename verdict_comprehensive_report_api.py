from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from pydantic import BaseModel
from fastapi.testclient import TestClient
import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: Dict[str, float]

class VerdictReport(BaseModel):
    server_id: str
    server_name: str
    verdict: str
    risk_tier: str
    axes: List[AxisScore]
    criteria_version: str
    scored_at: str

def get_risk_tier(axes: List[AxisScore]) -> str:
    for axis in axes:
        if axis.axis_name == 'overall_risk':
            continue
        if axis.p_critical > 0.5:
            return 'CRITICAL'
    return 'HIGH'

@router.get("/servers/{server_id}/verdict-report", response_model=VerdictReport)
async def get_verdict_report(server_id: str, session: Session = Depends(get_session)) -> VerdictReport:
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
    if not axes:
        raise HTTPException(status_code=404, detail="No risk axes found for server")

    axes_dict = {axis.axis_name: axis for axis in axes}
    required_axes = ['overall_risk', 'auth_strength', 'capability_breadth', 'data_sensitivity',
                     'network_egress', 'maintainer_trust', 'exploit_surface']
    for axis_name in required_axes:
        if axis_name not in axes_dict:
            raise HTTPException(status_code=404, detail=f"Missing risk axis: {axis_name}")

    axes_list = []
    for axis_name in required_axes:
        axis = axes_dict[axis_name]
        axes_list.append(AxisScore(
            axis_name=axis.axis_name,
            label=axis.label,
            p_top=axis.p_top,
            p_critical=axis.p_critical,
            p_danger=axis.p_danger,
            probs=axis.probs
        ))

    risk_tier = get_risk_tier(axes_list)
    if server.risk_tier != risk_tier:
        risk_tier = server.risk_tier

    return VerdictReport(
        server_id=server.server_id,
        server_name=server.server_name,
        verdict=server.verdict,
        risk_tier=risk_tier,
        axes=axes_list,
        criteria_version=server.criteria_version,
        scored_at=server.scored_at
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import Base
    from app.models import MCPServerRegistry, MCPLLMAxisScores

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    test_server = MCPServerRegistry(
        server_id="test-server-1",
        server_name="Test Server 1",
        verdict="Pass",
        risk_tier="HIGH",
        criteria_version="1.0",
        scored_at="2023-01-01T00:00:00Z"
    )
    session.add(test_server)

    test_axes = [
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="overall_risk",
            label="Overall Risk",
            p_top=0.9,
            p_critical=0.1,
            p_danger=0.0,
            probs={"top": 0.9, "critical": 0.1, "danger": 0.0}
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="auth_strength",
            label="Auth Strength",
            p_top=0.8,
            p_critical=0.2,
            p_danger=0.0,
            probs={"top": 0.8, "critical": 0.2, "danger": 0.0}
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="capability_breadth",
            label="Capability Breadth",
            p_top=0.7,
            p_critical=0.3,
            p_danger=0.0,
            probs={"top": 0.7, "critical": 0.3, "danger": 0.0}
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="data_sensitivity",
            label="Data Sensitivity",
            p_top=0.6,
            p_critical=0.4,
            p_danger=0.0,
            probs={"top": 0.6, "critical": 0.4, "danger": 0.0}
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="network_egress",
            label="Network Egress",
            p_top=0.5,
            p_critical=0.5,
            p_danger=0.0,
            probs={"top": 0.5, "critical": 0.5, "danger": 0.0}
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="maintainer_trust",
            label="Maintainer Trust",
            p_top=0.4,
            p_critical=0.6,
            p_danger=0.0,
            probs={"top": 0.4, "critical": 0.6, "danger": 0.0}
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="exploit_surface",
            label="Exploit Surface",
            p_top=0.3,
            p_critical=0.7,
            p_danger=0.0,
            probs={"top": 0.3, "critical": 0.7, "danger": 0.0}
        )
    ]
    session.add_all(test_axes)
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test-server-1/verdict-report")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test-server-1"
    assert data["server_name"] == "Test Server 1"
    assert data["risk_tier"] == "CRITICAL"  # Override due to CRITICAL axis
    assert len(data["axes"]) == 7
    print("PASS")