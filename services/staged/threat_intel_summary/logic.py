from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import ThreatIntelRef, vuln_advisories, VulnLink

router = APIRouter(prefix="/api/threat_intel")

class Indicator(BaseModel):
    type: str
    value: str
    source: str
    fetched_at: str

class Vulnerability(BaseModel):
    id: int
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: str
    match_confidence: str

class ThreatIntelSummary(BaseModel):
    server_id: str
    indicators: List[Indicator]
    vulnerabilities: List[Vulnerability]

@router.get("/summary", response_model=ThreatIntelSummary)
async def get_threat_intel_summary(server_id: str, session: Session = Depends(get_session)):
    # Get indicators
    indicators = session.query(
        ThreatIntelRef.indicator_type,
        ThreatIntelRef.indicator_value,
        ThreatIntelRef.source,
        ThreatIntelRef.fetched_at
    ).distinct().all()

    # Get vulnerabilities for the server
    vulnerabilities = session.query(
        vuln_advisories.id,
        vuln_advisories.summary,
        vuln_advisories.severity,
        vuln_advisories.ecosystem,
        vuln_advisories.package,
        vuln_advisories.published_at,
        VulnLink.match_confidence
    ).join(
        VulnLink, vuln_advisories.id == VulnLink.advisory_id
    ).filter(
        VulnLink.server_id == server_id
    ).all()

    return {
        "server_id": server_id,
        "indicators": [{"type": i.type, "value": i.value, "source": i.source, "fetched_at": i.fetched_at} for i in indicators],
        "vulnerabilities": [{"id": v.id, "summary": v.summary, "severity": v.severity, "ecosystem": v.ecosystem, "package": v.package, "published_at": v.published_at, "match_confidence": v.match_confidence} for v in vulnerabilities]
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import ThreatIntelRef, vuln_advisories, VulnLink

    # Create in-memory SQLite database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)

    # Add test data
    from sqlalchemy.orm import sessionmaker
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Add test ThreatIntelRef
    test_session.add(ThreatIntelRef(
        indicator_type="IP",
        indicator_value="192.168.1.1",
        pulse_name="Test Pulse",
        source="Test Source",
        fetched_at="2023-01-01"
    ))

    # Add test vuln_advisories
    test_advisory = vuln_advisories(
        id=1,
        summary="Test Vulnerability",
        severity="High",
        ecosystem="Python",
        package="test-package",
        published_at="2023-01-01"
    )
    test_session.add(test_advisory)

    # Add test VulnLink
    test_session.add(VulnLink(
        server_id="srv1",
        advisory_id=1,
        match_confidence="High"
    ))

    test_session.commit()

    # Override the dependency for testing
    from app.db import get_session
    app.dependency_overrides[get_session] = lambda: test_session

    # Create FastAPI app and test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/threat_intel/summary?server_id=srv1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["indicators"]) == 1
    assert len(data["vulnerabilities"]) == 1
    print("PASS")