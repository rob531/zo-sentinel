from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

class RiskAxis(BaseModel):
    label: str
    p_top: float

class RiskVerdict(BaseModel):
    axes: Dict[str, RiskAxis]
    overall: float
    risk_tier: str
    verdict: str
    registry_source: str
    url: str

def get_risk_verdict(server_id: int, session: Session = Depends(get_session)) -> RiskVerdict:
    # Get server registry info
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores
    axis_scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).first()
    if not axis_scores:
        raise HTTPException(status_code=404, detail="Risk scores not found for server")

    # Determine risk tier with CRITICAL override
    axes = {
        'critical': RiskAxis(label='Critical', p_top=axis_scores.critical),
        'high': RiskAxis(label='High', p_top=axis_scores.high),
        'medium': RiskAxis(label='Medium', p_top=axis_scores.medium),
        'low': RiskAxis(label='Low', p_top=axis_scores.low),
        'informational': RiskAxis(label='Informational', p_top=axis_scores.informational),
        'unknown': RiskAxis(label='Unknown', p_top=axis_scores.unknown)
    }

    # Determine overall risk tier
    if axis_scores.critical > 0.5:
        risk_tier = 'CRITICAL'
    elif axis_scores.overall_risk > 0.75:
        risk_tier = 'HIGH'
    elif axis_scores.overall_risk > 0.5:
        risk_tier = 'MEDIUM'
    elif axis_scores.overall_risk > 0.25:
        risk_tier = 'LOW'
    else:
        risk_tier = 'MINIMAL'

    # Determine verdict
    verdict = "High risk" if risk_tier in ['CRITICAL', 'HIGH'] else "Low risk"

    return RiskVerdict(
        axes=axes,
        overall=axis_scores.overall_risk,
        risk_tier=risk_tier,
        verdict=verdict,
        registry_source=server.source,
        url=server.url
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = McpServerRegistry(
        id=1,
        name="Test Server",
        url="https://test.example.com",
        source="test_source"
    )
    test_session.add(test_server)
    test_session.commit()

    test_scores = McpLlmAxisScore(
        server_id=1,
        critical=0.6,
        high=0.3,
        medium=0.2,
        low=0.1,
        informational=0.05,
        unknown=0.05,
        overall_risk=0.8
    )
    test_session.add(test_scores)
    test_session.commit()

    # Test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/api/servers/1/risk")
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert set(data['axes'].keys()) == {'critical', 'high', 'medium', 'low', 'informational', 'unknown'}
    assert data['risk_tier'] == 'CRITICAL'  # Should be overridden by critical axis
    assert data['registry_source'] == 'test_source'
    assert data['url'] == 'https://test.example.com'

    print("PASS")