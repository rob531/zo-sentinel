from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()

class RiskAxis(BaseModel):
    label: str
    p_top: float

class RiskVerdict(BaseModel):
    axes: Dict[str, RiskAxis]
    overall: float
    risk_tier: str
    verdict: str
    registry_source: str
    url: Optional[str]

def determine_risk_tier(overall: float, axes: Dict[str, float]) -> str:
    if any(p_top >= 0.9 for p_top in axes.values()):
        return "CRITICAL"
    elif overall >= 0.8:
        return "HIGH"
    elif overall >= 0.6:
        return "MEDIUM"
    elif overall >= 0.4:
        return "LOW"
    else:
        return "MINIMAL"

def get_verdict(p_top: float) -> str:
    if p_top >= 0.9:
        return "CRITICAL"
    elif p_top >= 0.8:
        return "HIGH"
    elif p_top >= 0.6:
        return "MEDIUM"
    elif p_top >= 0.4:
        return "LOW"
    else:
        return "MINIMAL"

@router.get("/api/servers/{server_id}/risk", response_model=RiskVerdict)
async def get_risk_verdict(server_id: int, session: Session = Depends(get_session)):
    # Get server registry info
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get LLM axis scores
    axis_scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).first()
    if not axis_scores:
        raise HTTPException(status_code=404, detail="Risk scores not found for server")

    # Calculate overall risk
    axes = {
        "malware": RiskAxis(label="Malware", p_top=axis_scores.malware_p_top),
        "vulnerability": RiskAxis(label="Vulnerability", p_top=axis_scores.vulnerability_p_top),
        "misconfiguration": RiskAxis(label="Misconfiguration", p_top=axis_scores.misconfiguration_p_top),
        "anomaly": RiskAxis(label="Anomaly", p_top=axis_scores.anomaly_p_top),
        "threat_intel": RiskAxis(label="Threat Intel", p_top=axis_scores.threat_intel_p_top),
        "compliance": RiskAxis(label="Compliance", p_top=axis_scores.compliance_p_top)
    }

    overall = sum(axis.p_top for axis in axes.values()) / len(axes)
    risk_tier = determine_risk_tier(overall, {axis: axis_data.p_top for axis, axis_data in axes.items()})
    verdict = get_verdict(overall)

    return RiskVerdict(
        axes=axes,
        overall=overall,
        risk_tier=risk_tier,
        verdict=verdict,
        registry_source=server.source,
        url=server.url
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Set up test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    test_session = TestSession()
    test_server = McpServerRegistry(
        id=1,
        name="Test Server",
        source="test_source",
        url="http://test-server.com"
    )
    test_session.add(test_server)
    test_session.commit()

    test_scores = McpLlmAxisScore(
        server_id=1,
        malware_p_top=0.95,
        vulnerability_p_top=0.8,
        misconfiguration_p_top=0.7,
        anomaly_p_top=0.6,
        threat_intel_p_top=0.5,
        compliance_p_top=0.4
    )
    test_session.add(test_scores)
    test_session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/api/servers/1/risk")
    assert response.status_code == 200
    data = response.json()

    # Verify response
    assert len(data["axes"]) == 6
    assert data["risk_tier"] == "CRITICAL"  # Should be overridden due to malware_p_top >= 0.9
    assert data["registry_source"] == "test_source"
    assert data["url"] == "http://test-server.com"

    print("PASS")