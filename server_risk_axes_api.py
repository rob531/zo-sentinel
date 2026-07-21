from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Dict, Optional
import httpx
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool

class RiskAxesResponse(BaseModel):
    axes: Dict[str, AxisScore]

def get_server_risk_axes(server_id: str, version: Optional[str] = None) -> dict:
    # Query the write_service for axis scores
    query = """
    SELECT axis_name, label, p_top, p_critical, p_danger, escalated
    FROM mcp_llm_axis_scores
    WHERE server_id = ?
    """
    params = [server_id]
    if version:
        query += " AND decision_rule_version = ?"
        params.append(version)

    try:
        response = httpx.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": params}
        )
        response.raise_for_status()
        rows = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Server not found")
        raise

    # Format the response
    axes = {}
    for row in rows:
        axes[row['axis_name']] = {
            'label': row['label'],
            'p_top': row['p_top'],
            'p_critical': row['p_critical'],
            'p_danger': row['p_danger'],
            'escalated': bool(row['escalated'])
        }

    return {"axes": axes}

@router.get("/servers/{server_id}/risk_axes", response_model=RiskAxesResponse)
async def read_server_risk_axes(
    server_id: str,
    version: Optional[str] = Query(None),
    db_session=Depends(get_session)
):
    # Verify server exists in registry
    if not db_session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first():
        raise HTTPException(status_code=404, detail="Server not found")

    return get_server_risk_axes(server_id, version)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import MCPServerRegistry, MCPLLMAxisScores
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Create tables
    MCPServerRegistry.__table__.create(test_engine)
    MCPLLMAxisScores.__table__.create(test_engine)

    # Add test data
    test_server = MCPServerRegistry(server_id="abc123", name="Test Server")
    test_session.add(test_server)

    test_axes = [
        MCPLLMAxisScores(
            server_id="abc123",
            axis_name="overall_risk",
            label="Overall Risk",
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            escalated=True,
            decision_rule_version="1.0"
        ),
        MCPLLMAxisScores(
            server_id="abc123",
            axis_name="auth_strength",
            label="Authentication Strength",
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6,
            escalated=False,
            decision_rule_version="1.0"
        ),
        MCPLLMAxisScores(
            server_id="abc123",
            axis_name="capability_breadth",
            label="Capability Breadth",
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            escalated=False,
            decision_rule_version="1.0"
        ),
        MCPLLMAxisScores(
            server_id="abc123",
            axis_name="data_sensitivity",
            label="Data Sensitivity",
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            escalated=False,
            decision_rule_version="1.0"
        ),
        MCPLLMAxisScores(
            server_id="abc123",
            axis_name="network_egress",
            label="Network Egress",
            p_top=0.5,
            p_critical=0.4,
            p_danger=0.3,
            escalated=False,
            decision_rule_version="1.0"
        ),
        MCPLLMAxisScores(
            server_id="abc123",
            axis_name="maintainer_trust",
            label="Maintainer Trust",
            p_top=0.4,
            p_critical=0.3,
            p_danger=0.2,
            escalated=False,
            decision_rule_version="1.0"
        ),
        MCPLLMAxisScores(
            server_id="abc123",
            axis_name="exploit_surface",
            label="Exploit Surface",
            p_top=0.3,
            p_critical=0.2,
            p_danger=0.1,
            escalated=False,
            decision_rule_version="1.0"
        )
    ]
    test_session.add_all(test_axes)
    test_session.commit()

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/servers/abc123/risk_axes")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 7
    assert all(isinstance(axis, dict) for axis in data["axes"].values())
    assert all("label" in axis for axis in data["axes"].values())
    assert all("p_top" in axis for axis in data["axes"].values())
    assert all("p_critical" in axis for axis in data["axes"].values())
    assert all("p_danger" in axis for axis in data["axes"].values())
    assert all("escalated" in axis for axis in data["axes"].values())
    assert data["axes"]["overall_risk"]["escalated"] is True
    assert data["axes"]["auth_strength"]["escalated"] is False

    print("PASS")