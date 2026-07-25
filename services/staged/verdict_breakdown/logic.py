from fastapi import Depends
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel

class AxisBreakdown(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: List[float]
    escalated: bool
    decision_rule_version: str
    scored_at: str

class VerdictBreakdownResponse(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str
    confidence: float
    last_assessed: str
    axes: List[AxisBreakdown]

def get_verdict_breakdown(server_id: str, session: Session = Depends(get_session)) -> VerdictBreakdownResponse:
    # Get server metadata
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise ValueError(f"Server {server_id} not found")

    # Get axis scores
    axes = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    # Prepare axis breakdowns
    axis_breakdowns = []
    for axis in axes:
        axis_breakdowns.append(AxisBreakdown(
            axis_name=axis.axis_name,
            label=axis.label,
            p_top=axis.p_top,
            p_critical=axis.p_critical,
            p_danger=axis.p_danger,
            probs=axis.probs,
            escalated=axis.escalated,
            decision_rule_version=axis.decision_rule_version,
            scored_at=axis.scored_at.isoformat() if axis.scored_at else None
        ))

    # Prepare response
    return VerdictBreakdownResponse(
        server_id=server.server_id,
        name=server.name,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        confidence=server.confidence,
        last_assessed=server.last_assessed.isoformat() if server.last_assessed else None,
        axes=axis_breakdowns
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from datetime import datetime

    # Setup test database
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    def seed_test_data():
        session = SessionLocal()
        try:
            # Seed server registry
            servers = [
                McpServerRegistry(
                    server_id="server1",
                    name="Test Server 1",
                    verdict="safe",
                    risk_tier="low",
                    confidence=0.95,
                    last_assessed=datetime.now()
                ),
                McpServerRegistry(
                    server_id="server2",
                    name="Test Server 2",
                    verdict="moderate",
                    risk_tier="medium",
                    confidence=0.85,
                    last_assessed=datetime.now()
                ),
                McpServerRegistry(
                    server_id="server3",
                    name="Test Server 3",
                    verdict="high",
                    risk_tier="high",
                    confidence=0.75,
                    last_assessed=datetime.now()
                )
            ]
            session.add_all(servers)

            # Seed axis scores
            axes = [
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="overall_risk",
                    label="Overall Risk",
                    p_top=0.1,
                    p_critical=0.2,
                    p_danger=0.3,
                    probs=[0.1, 0.2, 0.3, 0.4],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="auth_strength",
                    label="Auth Strength",
                    p_top=0.2,
                    p_critical=0.3,
                    p_danger=0.4,
                    probs=[0.2, 0.3, 0.4, 0.1],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="capability_breadth",
                    label="Capability Breadth",
                    p_top=0.3,
                    p_critical=0.4,
                    p_danger=0.5,
                    probs=[0.3, 0.4, 0.5, 0.2],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="data_sensitivity",
                    label="Data Sensitivity",
                    p_top=0.4,
                    p_critical=0.5,
                    p_danger=0.6,
                    probs=[0.4, 0.5, 0.6, 0.3],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="network_egress",
                    label="Network Egress",
                    p_top=0.5,
                    p_critical=0.6,
                    p_danger=0.7,
                    probs=[0.5, 0.6, 0.7, 0.4],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="maintainer_trust",
                    label="Maintainer Trust",
                    p_top=0.6,
                    p_critical=0.7,
                    p_danger=0.8,
                    probs=[0.6, 0.7, 0.8, 0.5],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="exploit_surface",
                    label="Exploit Surface",
                    p_top=0.7,
                    p_critical=0.8,
                    p_danger=0.9,
                    probs=[0.7, 0.8, 0.9, 0.6],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="overall_risk",
                    label="Overall Risk",
                    p_top=0.2,
                    p_critical=0.3,
                    p_danger=0.4,
                    probs=[0.2, 0.3, 0.4, 0.1],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="auth_strength",
                    label="Auth Strength",
                    p_top=0.3,
                    p_critical=0.4,
                    p_danger=0.5,
                    probs=[0.3, 0.4, 0.5, 0.2],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="capability_breadth",
                    label="Capability Breadth",
                    p_top=0.4,
                    p_critical=0.5,
                    p_danger=0.6,
                    probs=[0.4, 0.5, 0.6, 0.3],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="data_sensitivity",
                    label="Data Sensitivity",
                    p_top=0.5,
                    p_critical=0.6,
                    p_danger=0.7,
                    probs=[0.5, 0.6, 0.7, 0.4],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="network_egress",
                    label="Network Egress",
                    p_top=0.6,
                    p_critical=0.7,
                    p_danger=0.8,
                    probs=[0.6, 0.7, 0.8, 0.5],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="maintainer_trust",
                    label="Maintainer Trust",
                    p_top=0.7,
                    p_critical=0.8,
                    p_danger=0.9,
                    probs=[0.7, 0.8, 0.9, 0.6],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="exploit_surface",
                    label="Exploit Surface",
                    p_top=0.8,
                    p_critical=0.9,
                    p_danger=1.0,
                    probs=[0.8, 0.9, 1.0, 0.7],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server3",
                    axis_name="overall_risk",
                    label="Overall Risk",
                    p_top=0.3,
                    p_critical=0.4,
                    p_danger=0.5,
                    probs=[0.3, 0.4, 0.5, 0.2],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server3",
                    axis_name="auth_strength",
                    label="Auth Strength",
                    p_top=0.4,
                    p_critical=0.5,
                    p_danger=0.6,
                    probs=[0.4, 0.5, 0.6, 0.3],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server3",
                    axis_name="capability_breadth",
                    label="Capability Breadth",
                    p_top=0.5,
                    p_critical=0.6,
                    p_danger=0.7,
                    probs=[0.5, 0.6, 0.7, 0.4],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server3",
                    axis_name="data_sensitivity",
                    label="Data Sensitivity",
                    p_top=0.6,
                    p_critical=0.7,
                    p_danger=0.8,
                    probs=[0.6, 0.7, 0.8, 0.5],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server3",
                    axis_name="network_egress",
                    label="Network Egress",
                    p_top=0.7,
                    p_critical=0.8,
                    p_danger=0.9,
                    probs=[0.7, 0.8, 0.9, 0.6],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server3",
                    axis_name="maintainer_trust",
                    label="Maintainer Trust",
                    p_top=0.8,
                    p_critical=0.9,
                    p_danger=1.0,
                    probs=[0.8, 0.9, 1.0, 0.7],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                ),
                McpLlmAxisScore(
                    server_id="server3",
                    axis_name="exploit_surface",
                    label="Exploit Surface",
                    p_top=0.9,
                    p_critical=1.0,
                    p_danger=1.0,
                    probs=[0.9, 1.0, 1.0, 0.8],
                    escalated=False,
                    decision_rule_version="1.0",
                    scored_at=datetime.now()
                )
            ]
            session.add_all(axes)
            session.commit()
        finally:
            session.close()

    seed_test_data()

    # Test the endpoint
    @app.get("/api/verdict/{server_id}/breakdown")
    async def test_breakdown(server_id: str, session: Session = Depends(get_session)):
        return get_verdict_breakdown(server_id, session)

    client = TestClient(app)

    # Test with server1
    response = client.get("/api/verdict/server1/breakdown")
    assert response.status_code == 200
    data = response.json()
    assert "server_id" in data
    assert "name" in data
    assert "verdict" in data
    assert "risk_tier" in data
    assert "confidence" in data
    assert "last_assessed" in data
    assert "axes" in data
    assert len(data["axes"]) == 7
    axis_names = [axis["axis_name"] for axis in data["axes"]]
    assert "overall_risk" in axis_names
    assert "auth_strength" in axis_names
    assert "capability_breadth" in axis_names
    assert "data_sensitivity" in axis_names
    assert "network_egress" in axis_names
    assert "maintainer_trust" in axis_names
    assert "exploit_surface" in axis_names

    print("PASS")