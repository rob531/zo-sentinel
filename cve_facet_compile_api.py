from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPLLMAxisScores, VulnAdvisories

app = FastAPI()

class CVEFacetSummary(BaseModel):
    server_id: str
    cve_count: int
    high_severity: int
    medium_severity: int
    low_severity: int
    top_cves: List[str]

def get_cve_facet_summary(server_id: str, db: Session = Depends(get_session)) -> Dict:
    # Query exploit_surface scores
    exploit_surface_score = db.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.axis_name == 'exploit_surface',
        MCPLLMAxisScores.server_id == server_id
    ).first()

    if not exploit_surface_score:
        raise HTTPException(status_code=404, detail="Server not found or no exploit surface data")

    # Query vulnerability advisories
    advisories = db.query(VulnAdvisories).filter(
        VulnAdvisories.server_id == server_id
    ).all()

    # Count severities
    severity_counts = {
        'high_severity': 0,
        'medium_severity': 0,
        'low_severity': 0
    }

    for advisory in advisories:
        if advisory.severity == 'high':
            severity_counts['high_severity'] += 1
        elif advisory.severity == 'medium':
            severity_counts['medium_severity'] += 1
        elif advisory.severity == 'low':
            severity_counts['low_severity'] += 1

    # Get top 5 CVEs by severity (high first, then medium, then low)
    sorted_advisories = sorted(
        advisories,
        key=lambda x: {'high': 3, 'medium': 2, 'low': 1}.get(x.severity, 0),
        reverse=True
    )
    top_cves = [adv.cve_id for adv in sorted_advisories[:5]]

    return {
        'server_id': server_id,
        'cve_count': len(advisories),
        **severity_counts,
        'top_cves': top_cves
    }

@app.get("/cve-facet-summary/{server_id}", response_model=CVEFacetSummary)
async def cve_facet_summary(server_id: str, db: Session = Depends(get_session)):
    return get_cve_facet_summary(server_id, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Insert test data
    test_session = TestSession()
    test_session.add(MCPLLMAxisScores(
        server_id="test-server-1",
        axis_name="exploit_surface",
        score=0.8
    ))
    test_session.add_all([
        VulnAdvisories(
            server_id="test-server-1",
            cve_id="CVE-2023-1234",
            severity="high"
        ),
        VulnAdvisories(
            server_id="test-server-1",
            cve_id="CVE-2023-5678",
            severity="medium"
        ),
        VulnAdvisories(
            server_id="test-server-1",
            cve_id="CVE-2023-9012",
            severity="low"
        ),
        VulnAdvisories(
            server_id="test-server-1",
            cve_id="CVE-2023-3456",
            severity="high"
        ),
        VulnAdvisories(
            server_id="test-server-1",
            cve_id="CVE-2023-7890",
            severity="medium"
        ),
        VulnAdvisories(
            server_id="test-server-1",
            cve_id="CVE-2023-2345",
            severity="low"
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/cve-facet-summary/test-server-1")
    assert response.status_code == 200
    assert response.json() == {
        "server_id": "test-server-1",
        "cve_count": 6,
        "high_severity": 2,
        "medium_severity": 2,
        "low_severity": 2,
        "top_cves": ["CVE-2023-1234", "CVE-2023-3456", "CVE-2023-5678", "CVE-2023-7890", "CVE-2023-9012"]
    }

    print("PASS")