from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.pool import StaticPool

router = APIRouter(prefix="/api")

class ServerAnalysis(BaseModel):
    server_id: int
    server_name: str
    advisory_count: int
    highest_cvss: Optional[float]

class AdvisorySummary(BaseModel):
    advisory_id: int
    title: str
    cvss: Optional[float]
    server_count: int

@router.get("/vuln/analysis", response_model=List[ServerAnalysis])
def get_vuln_analysis(db: Session = Depends(get_session)):
    results = db.query(
        McpServerRegistry.id.label('server_id'),
        McpServerRegistry.name.label('server_name'),
        func.count(VulnLink.advisory_id).label('advisory_count'),
        func.max(VulnAdvisory.cvss).label('highest_cvss')
    ).join(
        VulnLink, McpServerRegistry.id == VulnLink.server_id
    ).join(
        VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id
    ).group_by(
        McpServerRegistry.id, McpServerRegistry.name
    ).all()

    return [ServerAnalysis(**result._asdict()) for result in results]

@router.get("/vuln/servers", response_model=List[AdvisorySummary])
def get_vuln_servers(db: Session = Depends(get_session)):
    results = db.query(
        VulnAdvisory.id.label('advisory_id'),
        VulnAdvisory.title,
        VulnAdvisory.cvss,
        func.count(VulnLink.server_id).label('server_count')
    ).join(
        VulnLink, VulnAdvisory.id == VulnLink.advisory_id
    ).group_by(
        VulnAdvisory.id, VulnAdvisory.title, VulnAdvisory.cvss
    ).all()

    return [AdvisorySummary(**result._asdict()) for result in results]

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    # Override the session for testing
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=Base.metadata.create_all().bind,
        autocommit=True,
        autoflush=False,
        expire_on_commit=False
    )

    # Create test data
    test_session = Session(
        bind=Base.metadata.create_all().bind,
        autocommit=True,
        autoflush=False,
        expire_on_commit=False
    )

    # Add test servers
    test_server1 = McpServerRegistry(name="Test Server 1")
    test_server2 = McpServerRegistry(name="Test Server 2")
    test_session.add_all([test_server1, test_server2])
    test_session.commit()

    # Add test advisories
    test_advisory1 = VulnAdvisory(title="Test Advisory 1", cvss=7.5)
    test_advisory2 = VulnAdvisory(title="Test Advisory 2", cvss=9.0)
    test_session.add_all([test_advisory1, test_advisory2])
    test_session.commit()

    # Add test links
    test_link1 = VulnLink(server_id=test_server1.id, advisory_id=test_advisory1.id)
    test_link2 = VulnLink(server_id=test_server1.id, advisory_id=test_advisory2.id)
    test_link3 = VulnLink(server_id=test_server2.id, advisory_id=test_advisory1.id)
    test_session.add_all([test_link1, test_link2, test_link3])
    test_session.commit()

    # Run tests
    client = TestClient(test_app)

    analysis_response = client.get("/api/vuln/analysis")
    assert analysis_response.status_code == 200
    assert len(analysis_response.json()) > 0

    servers_response = client.get("/api/vuln/servers")
    assert servers_response.status_code == 200
    assert len(servers_response.json()) > 0

    print("PASS")