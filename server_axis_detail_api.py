from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry
from sqlalchemy.orm import Session
from datetime import datetime

router = APIRouter()

class AxisDetail(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    probs: dict
    escalated: bool
    decision_rule_version: str
    model_version: str
    scored_at: datetime

class ServerAxisDetailResponse(BaseModel):
    server_id: str
    server_name: str
    server_url: str
    trust_score: float
    verdict: str
    axes: List[AxisDetail]
    overall_score: float
    risk_tier: str

def calculate_risk_tier(overall_score: float) -> str:
    if overall_score > 75:
        return "TRUSTED_GENERAL"
    elif overall_score > 60:
        return "TRUSTED_RESEARCH"
    elif overall_score > 45:
        return "ENTERPRISE_CONTROLLED"
    elif overall_score > 30:
        return "CAUTION_LIMITED"
    elif overall_score > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

@router.get("/servers/{server_id}/axis-detail", response_model=ServerAxisDetailResponse)
async def get_server_axis_detail(server_id: str, db: Session = Depends(get_session)):
    # Fetch server metadata
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Fetch all 7 risk axes
    axes = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
    if not axes:
        raise HTTPException(status_code=404, detail="No axis scores found for this server")

    # Calculate overall score (weighted average of p_top values)
    overall_score = sum(axis.p_top for axis in axes) / len(axes)

    # Determine risk tier
    risk_tier = calculate_risk_tier(overall_score)

    # Prepare response
    response = ServerAxisDetailResponse(
        server_id=server.server_id,
        server_name=server.name,
        server_url=server.url,
        trust_score=server.trust_score,
        verdict=server.verdict,
        axes=[
            AxisDetail(
                axis_name=axis.axis_name,
                label=axis.label,
                label_index=axis.label_index,
                p_top=axis.p_top,
                p_critical=axis.p_critical,
                p_danger=axis.p_danger,
                probs=axis.probs,
                escalated=axis.escalated,
                decision_rule_version=axis.decision_rule_version,
                model_version=axis.model_version,
                scored_at=axis.scored_at
            ) for axis in axes
        ],
        overall_score=overall_score,
        risk_tier=risk_tier
    )

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test-id",
        name="Test Server",
        url="http://test-server.com",
        trust_score=70.0,
        verdict="APPROVED"
    )
    test_session.add(test_server)

    test_axes = [
        MCPLLMAxisScores(
            server_id="test-id",
            axis_name="overall_risk",
            label="Low Risk",
            label_index=0,
            p_top=0.8,
            p_critical=0.1,
            p_danger=0.1,
            probs={"Low Risk": 0.8, "Medium Risk": 0.1, "High Risk": 0.1},
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-id",
            axis_name="auth_strength",
            label="Strong",
            label_index=0,
            p_top=0.9,
            p_critical=0.05,
            p_danger=0.05,
            probs={"Strong": 0.9, "Medium": 0.05, "Weak": 0.05},
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-id",
            axis_name="capability_breadth",
            label="Narrow",
            label_index=0,
            p_top=0.7,
            p_critical=0.2,
            p_danger=0.1,
            probs={"Narrow": 0.7, "Medium": 0.2, "Broad": 0.1},
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-id",
            axis_name="data_sensitivity",
            label="Low",
            label_index=0,
            p_top=0.85,
            p_critical=0.1,
            p_danger=0.05,
            probs={"Low": 0.85, "Medium": 0.1, "High": 0.05},
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-id",
            axis_name="network_egress",
            label="Controlled",
            label_index=0,
            p_top=0.95,
            p_critical=0.03,
            p_danger=0.02,
            probs={"Controlled": 0.95, "Medium": 0.03, "Uncontrolled": 0.02},
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-id",
            axis_name="maintainer_trust",
            label="Trusted",
            label_index=0,
            p_top=0.9,
            p_critical=0.05,
            p_danger=0.05,
            probs={"Trusted": 0.9, "Medium": 0.05, "Untrusted": 0.05},
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-id",
            axis_name="exploit_surface",
            label="Small",
            label_index=0,
            p_top=0.8,
            p_critical=0.15,
            p_danger=0.05,
            probs={"Small": 0.8, "Medium": 0.15, "Large": 0.05},
            escalated=False,
            decision_rule_version="1.0",
            model_version="1.0",
            scored_at=datetime.now()
        )
    ]
    test_session.add_all(test_axes)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test-id/axis-detail")
    assert response.status_code == 200
    data = response.json()

    # Verify all 7 axes are present
    assert len(data["axes"]) == 7

    # Verify risk tier calculation
    overall_score = sum(axis["p_top"] for axis in data["axes"]) / 7
    if overall_score > 75:
        assert data["risk_tier"] == "TRUSTED_GENERAL"
    elif overall_score > 60:
        assert data["risk_tier"] == "TRUSTED_RESEARCH"
    elif overall_score > 45:
        assert data["risk_tier"] == "ENTERPRISE_CONTROLLED"
    elif overall_score > 30:
        assert data["risk_tier"] == "CAUTION_LIMITED"
    elif overall_score > 15:
        assert data["risk_tier"] == "HIGH_RISK_ISOLATED"
    else:
        assert data["risk_tier"] == "KNOWN_THREAT"

    print("PASS")