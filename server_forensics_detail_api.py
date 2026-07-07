from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
import requests
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, TrustOverrideStatus

router = APIRouter()

class AxisForensics(BaseModel):
    axis_name: str
    label: str
    label_index: int
    probs: Dict[str, float]
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    decision_rule_version: str
    model_version: str
    scored_at: datetime

class ThreatLink(BaseModel):
    advisory_id: str
    feed: str
    severity: str
    summary: str
    match_confidence: float
    linked_at: datetime

class ForensicsResponse(BaseModel):
    server_id: str
    name: str
    url: str
    registry_source: str
    verdict: str
    confidence: float
    trust_score: float
    risk_tier: str
    last_assessed: datetime
    axes: List[AxisForensics]
    threat_associations: List[ThreatLink]
    trust_override: TrustOverrideStatus
    scored_at: datetime

def query_write_service(query: str, params: Dict = None):
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}},
        timeout=5
    )
    response.raise_for_status()
    return response.json()

async def get_server_forensics(server_id: str, session=Depends(get_session)):
    # Fetch server registry data
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Fetch LLM axis scores
    axes = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
    if not axes:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    # Fetch threat associations
    threat_query = """
    SELECT advisory_id, feed, severity, summary, match_confidence, linked_at
    FROM mcp_vuln_links
    WHERE server_id = :server_id
    UNION ALL
    SELECT advisory_id, feed, severity, summary, match_confidence, linked_at
    FROM mcp_threat_intel_refs
    WHERE server_id = :server_id
    """
    threat_data = query_write_service(threat_query, {"server_id": server_id})
    threat_associations = [
        ThreatLink(**threat) for threat in threat_data
    ]

    # Fetch trust override status
    override = session.query(TrustOverrideStatus).filter(TrustOverrideStatus.server_id == server_id).first()
    trust_override = {
        "is_overridden": override.is_overridden if override else False,
        "source": override.source if override else None,
        "verdict": override.verdict if override else None
    }

    # Build response
    response = ForensicsResponse(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        registry_source=server.registry_source,
        verdict=server.verdict,
        confidence=server.confidence,
        trust_score=server.trust_score,
        risk_tier=server.risk_tier,
        last_assessed=server.last_assessed,
        axes=[
            AxisForensics(
                axis_name=axis.axis_name,
                label=axis.label,
                label_index=axis.label_index,
                probs=axis.probs,
                p_top=axis.p_top,
                p_critical=axis.p_critical,
                p_danger=axis.p_danger,
                escalated=axis.escalated,
                decision_rule_version=axis.decision_rule_version,
                model_version=axis.model_version,
                scored_at=axis.scored_at
            ) for axis in axes
        ],
        threat_associations=threat_associations,
        trust_override=trust_override,
        scored_at=datetime.utcnow()
    )

    return response

@router.get("/servers/{server_id}/forensics", response_model=ForensicsResponse)
async def get_forensics(server_id: str):
    try:
        return await get_server_forensics(server_id)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import MCPServerRegistry, MCPLLMAxisScores, TrustOverrideStatus
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Create tables
    MCPServerRegistry.__table__.create(test_engine)
    MCPLLMAxisScores.__table__.create(test_engine)
    TrustOverrideStatus.__table__.create(test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Seed test data
    server1 = MCPServerRegistry(
        server_id="test1",
        name="Test Server 1",
        url="http://test1.example.com",
        registry_source="manual",
        verdict="malicious",
        confidence=0.95,
        trust_score=0.1,
        risk_tier="high",
        last_assessed=datetime.utcnow()
    )
    test_session.add(server1)

    axis1 = MCPLLMAxisScores(
        server_id="test1",
        axis_name="behavioral",
        label="malicious",
        label_index=1,
        probs={"benign": 0.05, "suspicious": 0.1, "malicious": 0.85},
        p_top=0.85,
        p_critical=0.85,
        p_danger=0.85,
        escalated=True,
        decision_rule_version="1.0",
        model_version="1.0",
        scored_at=datetime.utcnow()
    )
    test_session.add(axis1)

    axis2 = MCPLLMAxisScores(
        server_id="test1",
        axis_name="network",
        label="benign",
        label_index=0,
        probs={"benign": 0.9, "suspicious": 0.05, "malicious": 0.05},
        p_top=0.9,
        p_critical=0.05,
        p_danger=0.05,
        escalated=False,
        decision_rule_version="1.0",
        model_version="1.0",
        scored_at=datetime.utcnow()
    )
    test_session.add(axis2)

    override1 = TrustOverrideStatus(
        server_id="test1",
        is_overridden=True,
        source="admin",
        verdict="benign"
    )
    test_session.add(override1)

    server2 = MCPServerRegistry(
        server_id="test2",
        name="Test Server 2",
        url="http://test2.example.com",
        registry_source="automatic",
        verdict="benign",
        confidence=0.99,
        trust_score=0.9,
        risk_tier="low",
        last_assessed=datetime.utcnow()
    )
    test_session.add(server2)

    axis3 = MCPLLMAxisScores(
        server_id="test2",
        axis_name="behavioral",
        label="benign",
        label_index=0,
        probs={"benign": 0.95, "suspicious": 0.03, "malicious": 0.02},
        p_top=0.95,
        p_critical=0.03,
        p_danger=0.02,
        escalated=False,
        decision_rule_version="1.0",
        model_version="1.0",
        scored_at=datetime.utcnow()
    )
    test_session.add(axis3)

    test_session.commit()

    # Mock write_service responses
    def mock_query_write_service(query: str, params: Dict = None):
        if "test1" in params.get("server_id", ""):
            return [
                {
                    "advisory_id": "CVE-2023-1234",
                    "feed": "nvd",
                    "severity": "critical",
                    "summary": "Remote code execution vulnerability",
                    "match_confidence": 0.95,
                    "linked_at": "2023-01-01T00:00:00Z"
                },
                {
                    "advisory_id": "CVE-2023-5678",
                    "feed": "cisa",
                    "severity": "high",
                    "summary": "Privilege escalation vulnerability",
                    "match_confidence": 0.85,
                    "linked_at": "2023-01-02T00:00:00Z"
                }
            ]
        return []

    app.dependency_overrides[query_write_service] = mock_query_write_service

    # Test the endpoint
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    client = TestClient(test_app)

    response = client.get("/servers/test1/forensics")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 2
    assert len(data["threat_associations"]) == 2
    assert data["axes"][0]["escalated"] is True
    assert data["trust_override"]["is_overridden"] is True

    print("PASS")