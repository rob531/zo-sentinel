from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
from app.db import get_session
from app.models import Server, VulnerabilityAdvisory, VulnerabilityLink
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

router = APIRouter()

class SeverityCounts(BaseModel):
    CRITICAL: int
    HIGH: int
    MEDIUM: int
    LOW: int

class EcosystemCounts(BaseModel):
    npm: int
    pypi: int
    rubygems: int
    maven: int
    nuget: int
    go: int
    crates: int
    alpine: int
    debian: int
    ubuntu: int
    rhel: int
    arch: int

class TopVulnerableServer(BaseModel):
    server_id: int
    name: str
    advisory_count: int
    highest_severity: str

class RecentAdvisory(BaseModel):
    id: int
    summary: str
    severity: str
    published_at: datetime
    matched_servers: int

class VulnerabilitySummary(BaseModel):
    total_advisories: int
    severity_counts: SeverityCounts
    ecosystem_counts: EcosystemCounts
    top_vulnerable_servers: List[TopVulnerableServer]
    recent_advisories: List[RecentAdvisory]

class ServerVulnerabilitySummary(BaseModel):
    server_id: int
    total: int
    critical: int
    high: int
    medium: int
    low: int
    latest_advisory: Optional[Dict[str, str]]

@router.get("/vuln-summary", response_model=VulnerabilitySummary)
async def get_vulnerability_summary(db: Session = Depends(get_session)):
    # Total advisories
    total_advisories = db.query(func.count(VulnerabilityAdvisory.id)).scalar()

    # Severity counts
    severity_counts = db.query(
        func.count(VulnerabilityAdvisory.id).label("count"),
        VulnerabilityAdvisory.severity
    ).group_by(VulnerabilityAdvisory.severity).all()

    severity_counts_dict = {
        "CRITICAL": 0,
        "HIGH": 0,
        "MEDIUM": 0,
        "LOW": 0
    }

    for count, severity in severity_counts:
        severity_counts_dict[severity] = count

    # Ecosystem counts
    ecosystem_counts = db.query(
        func.count(VulnerabilityAdvisory.id).label("count"),
        VulnerabilityAdvisory.ecosystem
    ).group_by(VulnerabilityAdvisory.ecosystem).all()

    ecosystem_counts_dict = {
        "npm": 0,
        "pypi": 0,
        "rubygems": 0,
        "maven": 0,
        "nuget": 0,
        "go": 0,
        "crates": 0,
        "alpine": 0,
        "debian": 0,
        "ubuntu": 0,
        "rhel": 0,
        "arch": 0
    }

    for count, ecosystem in ecosystem_counts:
        ecosystem_counts_dict[ecosystem] = count

    # Top vulnerable servers
    top_servers = db.query(
        Server.id.label("server_id"),
        Server.name,
        func.count(VulnerabilityLink.advisory_id).label("advisory_count"),
        func.max(VulnerabilityAdvisory.severity).label("highest_severity")
    ).join(
        VulnerabilityLink, Server.id == VulnerabilityLink.server_id
    ).join(
        VulnerabilityAdvisory, VulnerabilityLink.advisory_id == VulnerabilityAdvisory.id
    ).group_by(
        Server.id, Server.name
    ).order_by(
        desc("advisory_count")
    ).limit(10).all()

    top_servers_list = [
        TopVulnerableServer(
            server_id=server.server_id,
            name=server.name,
            advisory_count=server.advisory_count,
            highest_severity=server.highest_severity
        ) for server in top_servers
    ]

    # Recent advisories
    recent_advisories = db.query(
        VulnerabilityAdvisory.id,
        VulnerabilityAdvisory.summary,
        VulnerabilityAdvisory.severity,
        VulnerabilityAdvisory.published_at,
        func.count(VulnerabilityLink.advisory_id).label("matched_servers")
    ).join(
        VulnerabilityLink, VulnerabilityAdvisory.id == VulnerabilityLink.advisory_id
    ).group_by(
        VulnerabilityAdvisory.id,
        VulnerabilityAdvisory.summary,
        VulnerabilityAdvisory.severity,
        VulnerabilityAdvisory.published_at
    ).order_by(
        desc("published_at")
    ).limit(10).all()

    recent_advisories_list = [
        RecentAdvisory(
            id=advisory.id,
            summary=advisory.summary,
            severity=advisory.severity,
            published_at=advisory.published_at,
            matched_servers=advisory.matched_servers
        ) for advisory in recent_advisories
    ]

    return VulnerabilitySummary(
        total_advisories=total_advisories,
        severity_counts=SeverityCounts(**severity_counts_dict),
        ecosystem_counts=EcosystemCounts(**ecosystem_counts_dict),
        top_vulnerable_servers=top_servers_list,
        recent_advisories=recent_advisories_list
    )

@router.get("/vuln-summary/servers/{server_id}", response_model=ServerVulnerabilitySummary)
async def get_server_vulnerability_summary(server_id: int, db: Session = Depends(get_session)):
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Count advisories by severity
    severity_counts = db.query(
        func.count(VulnerabilityAdvisory.id).label("count"),
        VulnerabilityAdvisory.severity
    ).join(
        VulnerabilityLink, VulnerabilityAdvisory.id == VulnerabilityLink.advisory_id
    ).filter(
        VulnerabilityLink.server_id == server_id
    ).group_by(
        VulnerabilityAdvisory.severity
    ).all()

    severity_counts_dict = {
        "total": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0
    }

    for count, severity in severity_counts:
        severity_counts_dict[severity.lower()] = count
        severity_counts_dict["total"] += count

    # Latest advisory
    latest_advisory = db.query(
        VulnerabilityAdvisory.id,
        VulnerabilityAdvisory.summary,
        VulnerabilityAdvisory.severity,
        VulnerabilityAdvisory.published_at
    ).join(
        VulnerabilityLink, VulnerabilityAdvisory.id == VulnerabilityLink.advisory_id
    ).filter(
        VulnerabilityLink.server_id == server_id
    ).order_by(
        desc(VulnerabilityAdvisory.published_at)
    ).first()

    latest_advisory_dict = None
    if latest_advisory:
        latest_advisory_dict = {
            "id": latest_advisory.id,
            "summary": latest_advisory.summary,
            "severity": latest_advisory.severity,
            "published_at": latest_advisory.published_at.isoformat()
        }

    return ServerVulnerabilitySummary(
        server_id=server_id,
        total=severity_counts_dict["total"],
        critical=severity_counts_dict["critical"],
        high=severity_counts_dict["high"],
        medium=severity_counts_dict["medium"],
        low=severity_counts_dict["low"],
        latest_advisory=latest_advisory_dict
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Create a test app
    app = FastAPI()
    app.include_router(router)

    # Override the get_session dependency for testing
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_db = SessionLocal()

    # Create test data
    Base.metadata.create_all(bind=engine)

    # Create test servers
    server1 = Server(name="Test Server 1")
    server2 = Server(name="Test Server 2")
    test_db.add_all([server1, server2])
    test_db.commit()

    # Create test advisories
    advisory1 = VulnerabilityAdvisory(
        summary="Test Advisory 1",
        severity="CRITICAL",
        ecosystem="npm",
        published_at=datetime.now()
    )
    advisory2 = VulnerabilityAdvisory(
        summary="Test Advisory 2",
        severity="HIGH",
        ecosystem="pypi",
        published_at=datetime.now()
    )
    test_db.add_all([advisory1, advisory2])
    test_db.commit()

    # Create test links
    link1 = VulnerabilityLink(server_id=server1.id, advisory_id=advisory1.id)
    link2 = VulnerabilityLink(server_id=server1.id, advisory_id=advisory2.id)
    link3 = VulnerabilityLink(server_id=server2.id, advisory_id=advisory1.id)
    test_db.add_all([link1, link2, link3])
    test_db.commit()

    # Override the get_session dependency
    app.dependency_overrides[get_session] = lambda: test_db

    # Test the endpoints
    client = TestClient(app)

    # Test GET /vuln-summary
    response = client.get("/vuln-summary")
    assert response.status_code == 200
    data = response.json()
    assert "severity_counts" in data
    assert "top_vulnerable_servers" in data
    assert len(data["top_vulnerable_servers"]) > 0

    # Test GET /vuln-summary/servers/{server_id}
    response = client.get("/vuln-summary/servers/1")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "critical" in data
    assert "high" in data
    assert "medium" in data
    assert "low" in data
    assert "latest_advisory" in data

    print("PASS")