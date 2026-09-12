from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api")

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
    match_confidence: float

class ThreatIntelSummary(BaseModel):
    server_id: str
    indicators: List[Indicator]
    vulnerabilities: List[Vulnerability]

@router.get("/threat_intel/summary", response_model=ThreatIntelSummary)
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
        VulnAdvisory.id,
        VulnAdvisory.summary,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.package,
        VulnAdvisory.published_at,
        VulnLink.match_confidence
    ).join(
        VulnLink, VulnAdvisory.id == VulnLink.advisory_id
    ).filter(
        VulnLink.server_id == server_id
    ).all()

    return {
        "server_id": server_id,
        "indicators": [{"type": i.indicator_type, "value": i.indicator_value, "source": i.source, "fetched_at": i.fetched_at} for i in indicators],
        "vulnerabilities": [{"id": v.id, "summary": v.summary, "severity": v.severity, "ecosystem": v.ecosystem, "package": v.package, "published_at": v.published_at, "match_confidence": v.match_confidence} for v in vulnerabilities]
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    session = SessionLocal()
    session.add(ThreatIntelRef(
        indicator_type="IP",
        indicator_value="192.168.1.1",
        pulse_name="Test Pulse",
        source="Test Source",
        fetched_at="2023-01-01"
    ))
    session.add(VulnAdvisory(
        id=1,
        summary="Test Vulnerability",
        severity="High",
        ecosystem="Python",
        package="test-package",
        published_at="2023-01-01"
    ))
    session.add(VulnLink(
        server_id="srv1",
        advisory_id=1,
        match_confidence=0.95
    ))
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/threat_intel/summary?server_id=srv1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["indicators"]) == 1
    assert len(data["vulnerabilities"]) == 1
    print("PASS")