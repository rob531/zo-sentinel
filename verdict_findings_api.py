from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, TrustGatingOverride

router = APIRouter()

class AxisEvidence(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool

class Finding(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    is_trusted_override: bool
    overall_risk: AxisEvidence
    auth_strength: AxisEvidence
    capability_breadth: AxisEvidence
    data_sensitivity: AxisEvidence
    network_egress: AxisEvidence
    maintainer_trust: AxisEvidence
    exploit_surface: AxisEvidence

@router.get("/verdicts/findings", response_model=List[Finding])
def get_verdict_findings(db: Session = Depends(get_session)):
    try:
        # Query server registry
        servers = db.query(MCPServerRegistry).all()

        findings = []
        for server in servers:
            # Get axis scores for this server
            axis_scores = db.query(MCPLLMAxisScores).filter(
                MCPLLMAxisScores.server_id == server.server_id
            ).first()

            if not axis_scores:
                continue

            # Get trust override if exists
            override = db.query(TrustGatingOverride).filter(
                TrustGatingOverride.server_id == server.server_id
            ).first()

            is_trusted_override = override.is_trusted if override else False
            published_overall_risk = override.published_overall_risk if override else None

            # Create axis evidence objects
            axes = {
                "overall_risk": AxisEvidence(
                    label="Overall Risk",
                    p_top=axis_scores.overall_risk_top,
                    p_critical=axis_scores.overall_risk_critical,
                    p_danger=axis_scores.overall_risk_danger,
                    escalated=published_overall_risk != axis_scores.overall_risk_top
                ),
                "auth_strength": AxisEvidence(
                    label="Auth Strength",
                    p_top=axis_scores.auth_strength_top,
                    p_critical=axis_scores.auth_strength_critical,
                    p_danger=axis_scores.auth_strength_danger,
                    escalated=False
                ),
                "capability_breadth": AxisEvidence(
                    label="Capability Breadth",
                    p_top=axis_scores.capability_breadth_top,
                    p_critical=axis_scores.capability_breadth_critical,
                    p_danger=axis_scores.capability_breadth_danger,
                    escalated=False
                ),
                "data_sensitivity": AxisEvidence(
                    label="Data Sensitivity",
                    p_top=axis_scores.data_sensitivity_top,
                    p_critical=axis_scores.data_sensitivity_critical,
                    p_danger=axis_scores.data_sensitivity_danger,
                    escalated=False
                ),
                "network_egress": AxisEvidence(
                    label="Network Egress",
                    p_top=axis_scores.network_egress_top,
                    p_critical=axis_scores.network_egress_critical,
                    p_danger=axis_scores.network_egress_danger,
                    escalated=False
                ),
                "maintainer_trust": AxisEvidence(
                    label="Maintainer Trust",
                    p_top=axis_scores.maintainer_trust_top,
                    p_critical=axis_scores.maintainer_trust_critical,
                    p_danger=axis_scores.maintainer_trust_danger,
                    escalated=False
                ),
                "exploit_surface": AxisEvidence(
                    label="Exploit Surface",
                    p_top=axis_scores.exploit_surface_top,
                    p_critical=axis_scores.exploit_surface_critical,
                    p_danger=axis_scores.exploit_surface_danger,
                    escalated=False
                )
            }

            findings.append(Finding(
                server_id=server.server_id,
                name=server.name,
                risk_tier=server.risk_tier,
                verdict=server.verdict,
                is_trusted_override=is_trusted_override,
                **axes
            ))

        return findings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import SessionLocal
    from app.models import Base
    from sqlalchemy import create_engine

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)

    # Override dependency for testing
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        # Add test server
        test_server = MCPServerRegistry(
            server_id="test1",
            name="Test Server",
            url="http://test.com",
            risk_tier="high",
            verdict="malicious",
            last_assessed="2023-01-01"
        )
        session.add(test_server)

        # Add test axis scores
        test_scores = MCPLLMAxisScores(
            server_id="test1",
            overall_risk_top=0.9,
            overall_risk_critical=0.8,
            overall_risk_danger=0.7,
            auth_strength_top=0.8,
            auth_strength_critical=0.7,
            auth_strength_danger=0.6,
            capability_breadth_top=0.7,
            capability_breadth_critical=0.6,
            capability_breadth_danger=0.5,
            data_sensitivity_top=0.8,
            data_sensitivity_critical=0.7,
            data_sensitivity_danger=0.6,
            network_egress_top=0.7,
            network_egress_critical=0.6,
            network_egress_danger=0.5,
            maintainer_trust_top=0.6,
            maintainer_trust_critical=0.5,
            maintainer_trust_danger=0.4,
            exploit_surface_top=0.7,
            exploit_surface_critical=0.6,
            exploit_surface_danger=0.5
        )
        session.add(test_scores)

        # Add test override
        test_override = TrustGatingOverride(
            server_id="test1",
            is_trusted=False,
            published_overall_risk=0.8
        )
        session.add(test_override)

        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/verdicts/findings")
    assert response.status_code == 200
    findings = response.json()
    assert len(findings) > 0
    for finding in findings:
        assert all(axis in finding for axis in [
            "overall_risk", "auth_strength", "capability_breadth",
            "data_sensitivity", "network_egress", "maintainer_trust",
            "exploit_surface"
        ])

    print("PASS")