from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpThreatAssociations, VulnLinks

router = APIRouter()

class ThreatAssociation(BaseModel):
    pulse_id: str
    pulse_name: str
    source: str
    linked_at: str

class VulnLink(BaseModel):
    advisory_id: str
    severity: str
    summary: str
    match_confidence: str

class ServerThreatIntelStatus(BaseModel):
    server_id: str
    threat_associations: List[ThreatAssociation]
    vuln_links: List[VulnLink]
    advisory_count: int
    threat_count: int

@router.get("/servers/{server_id}/threat-intel-status", response_model=ServerThreatIntelStatus)
async def get_server_threat_intel_status(
    server_id: str,
    session: Session = Depends(get_session)
):
    # Get threat associations
    threat_associations = session.query(McpThreatAssociations).filter(
        McpThreatAssociations.server_id == server_id
    ).all()

    threat_associations_list = [
        {
            "pulse_id": ta.pulse_id,
            "pulse_name": ta.pulse_name,
            "source": ta.source,
            "linked_at": ta.linked_at.isoformat() if ta.linked_at else None
        }
        for ta in threat_associations
    ]

    # Get vulnerability links
    vuln_links = session.query(VulnLinks).filter(
        VulnLinks.server_id == server_id
    ).all()

    vuln_links_list = [
        {
            "advisory_id": vl.advisory_id,
            "severity": vl.severity,
            "summary": vl.summary,
            "match_confidence": vl.match_confidence
        }
        for vl in vuln_links
    ]

    return {
        "server_id": server_id,
        "threat_associations": threat_associations_list,
        "vuln_links": vuln_links_list,
        "advisory_count": len(vuln_links_list),
        "threat_count": len(threat_associations_list)
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine, get_session
    from app.models import McpThreatAssociations, VulnLinks
    from sqlalchemy.orm import sessionmaker

    # Create a test database
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test data
    with TestSessionLocal() as session:
        session.add_all([
            McpThreatAssociations(
                server_id="test-server-1",
                pulse_id="pulse-1",
                pulse_name="Test Pulse 1",
                source="test-source",
                linked_at="2023-01-01T00:00:00"
            ),
            McpThreatAssociations(
                server_id="test-server-1",
                pulse_id="pulse-2",
                pulse_name="Test Pulse 2",
                source="test-source",
                linked_at="2023-01-02T00:00:00"
            ),
            VulnLinks(
                server_id="test-server-1",
                advisory_id="advisory-1",
                severity="high",
                summary="Test Advisory 1",
                match_confidence="high"
            ),
            VulnLinks(
                server_id="test-server-1",
                advisory_id="advisory-2",
                severity="medium",
                summary="Test Advisory 2",
                match_confidence="medium"
            )
        ])
        session.commit()

    # Create a test client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/servers/test-server-1/threat-intel-status")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test-server-1"
    assert len(data["threat_associations"]) == 2
    assert len(data["vuln_links"]) == 2
    assert data["advisory_count"] == 2
    assert data["threat_count"] == 2

    print("PASS")