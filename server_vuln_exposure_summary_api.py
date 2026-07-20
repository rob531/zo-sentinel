from fastapi import APIRouter, Depends, HTTPException
from typing import Dict
from app.db import get_session
from app.models import VulnAdvisories, VulnLinks
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

def get_vuln_exposure_summary(server_id: str, session: Session = Depends(get_session)) -> Dict:
    # Query vulnerability counts by severity
    vuln_counts = session.query(
        func.count(VulnAdvisories.id).label('total_vulns'),
        func.count(VulnAdvisories.id).filter(VulnAdvisories.severity == 'critical').label('critical_count'),
        func.count(VulnAdvisories.id).filter(VulnAdvisories.severity == 'high').label('high_count'),
        func.count(VulnAdvisories.id).filter(VulnAdvisories.severity == 'medium').label('medium_count'),
        func.count(VulnAdvisories.id).filter(VulnAdvisories.severity == 'low').label('low_count')
    ).join(
        VulnLinks, VulnAdvisories.id == VulnLinks.advisory_id
    ).filter(
        VulnLinks.server_id == server_id
    ).first()

    if not vuln_counts or vuln_counts.total_vulns == 0:
        raise HTTPException(status_code=404, detail="No vulnerabilities found for the server")

    # Calculate exposure score
    exposure_score = (
        (vuln_counts.critical_count * 1.0) +
        (vuln_counts.high_count * 0.75) +
        (vuln_counts.medium_count * 0.5) +
        (vuln_counts.low_count * 0.25)
    ) / vuln_counts.total_vulns * 100

    return {
        'total_vulns': vuln_counts.total_vulns,
        'critical_count': vuln_counts.critical_count,
        'high_count': vuln_counts.high_count,
        'medium_count': vuln_counts.medium_count,
        'low_count': vuln_counts.low_count,
        'exposure_score': round(exposure_score, 2)
    }

@router.get("/servers/{server_id}/vuln_exposure_summary", response_model=Dict)
async def vuln_exposure_summary(server_id: str, session: Session = Depends(get_session)):
    return get_vuln_exposure_summary(server_id, session)

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import get_session
    from app.models import VulnAdvisories, VulnLinks
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the get_session dependency for testing
    test_app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create tables
    VulnAdvisories.metadata.create_all(test_engine)
    VulnLinks.metadata.create_all(test_engine)

    # Insert test data
    with TestSessionLocal() as session:
        # Add test advisories
        adv1 = VulnAdvisories(id=1, severity='critical', description='Test critical vuln')
        adv2 = VulnAdvisories(id=2, severity='high', description='Test high vuln')
        adv3 = VulnAdvisories(id=3, severity='medium', description='Test medium vuln')
        adv4 = VulnAdvisories(id=4, severity='low', description='Test low vuln')
        session.add_all([adv1, adv2, adv3, adv4])

        # Add test links
        link1 = VulnLinks(advisory_id=1, server_id='test_server')
        link2 = VulnLinks(advisory_id=2, server_id='test_server')
        link3 = VulnLinks(advisory_id=3, server_id='test_server')
        link4 = VulnLinks(advisory_id=4, server_id='test_server')
        session.add_all([link1, link2, link3, link4])
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/servers/test_server/vuln_exposure_summary")
    assert response.status_code == 200
    data = response.json()
    assert data == {
        'total_vulns': 4,
        'critical_count': 1,
        'high_count': 1,
        'medium_count': 1,
        'low_count': 1,
        'exposure_score': 62.5
    }
    print("PASS")