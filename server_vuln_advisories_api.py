from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import VulnAdvisory, VulnLink, MCPServerRegistry
from datetime import datetime

router = APIRouter()

class AdvisorySummary(BaseModel):
    id: int
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    affected_ranges: str
    aliases: str
    source_url: str
    published_at: datetime
    match_confidence: str
    match_basis: str

class ServerAdvisoriesResponse(BaseModel):
    server_id: int
    advisories: List[AdvisorySummary]

class SearchResult(BaseModel):
    id: int
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: datetime
    matched_servers: int

class SearchResponse(BaseModel):
    results: List[SearchResult]

class FullAdvisoryResponse(BaseModel):
    id: int
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    affected_ranges: str
    aliases: str
    source_url: str
    published_at: datetime
    linked_servers: List[int]
    confidence_breakdown: Dict[str, int]

@router.get("/servers/{server_id}/vuln-advisories", response_model=ServerAdvisoriesResponse)
async def get_server_advisories(
    server_id: int,
    severity: Optional[str] = Query(None, description="Filter by severity (CRITICAL/HIGH/MEDIUM/LOW)"),
    session: Session = Depends(get_session)
):
    query = session.query(VulnAdvisory).join(VulnLink).filter(VulnLink.server_id == server_id)

    if severity:
        query = query.filter(VulnAdvisory.severity == severity)

    advisories = query.all()

    if not advisories:
        raise HTTPException(status_code=404, detail="No advisories found for this server")

    return {
        "server_id": server_id,
        "advisories": [
            {
                "id": adv.id,
                "feed": adv.feed,
                "summary": adv.summary,
                "severity": adv.severity,
                "ecosystem": adv.ecosystem,
                "package": adv.package,
                "affected_ranges": adv.affected_ranges,
                "aliases": adv.aliases,
                "source_url": adv.source_url,
                "published_at": adv.published_at,
                "match_confidence": adv.match_confidence,
                "match_basis": adv.match_basis
            }
            for adv in advisories
        ]
    }

@router.get("/vuln-advisories/search", response_model=SearchResponse)
async def search_advisories(
    q: Optional[str] = Query(None, description="Search term"),
    severity: Optional[str] = Query(None, description="Filter by severity (CRITICAL/HIGH/MEDIUM/LOW)"),
    ecosystem: Optional[str] = Query(None, description="Filter by ecosystem"),
    limit: int = Query(20, description="Limit results (max 100)"),
    session: Session = Depends(get_session)
):
    if limit > 100:
        limit = 100

    query = session.query(VulnAdvisory)

    if q:
        query = query.filter(
            (VulnAdvisory.summary.ilike(f"%{q}%")) |
            (VulnAdvisory.package.ilike(f"%{q}%")) |
            (VulnAdvisory.aliases.ilike(f"%{q}%"))
        )

    if severity:
        query = query.filter(VulnAdvisory.severity == severity)

    if ecosystem:
        query = query.filter(VulnAdvisory.ecosystem == ecosystem)

    advisories = query.limit(limit).all()

    results = []
    for adv in advisories:
        count = session.query(VulnLink).filter(VulnLink.advisory_id == adv.id).count()
        results.append({
            "id": adv.id,
            "feed": adv.feed,
            "summary": adv.summary,
            "severity": adv.severity,
            "ecosystem": adv.ecosystem,
            "package": adv.package,
            "published_at": adv.published_at,
            "matched_servers": count
        })

    return {"results": results}

@router.get("/vuln-advisories/{advisory_id}", response_model=FullAdvisoryResponse)
async def get_advisory_details(
    advisory_id: int,
    session: Session = Depends(get_session)
):
    advisory = session.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()

    if not advisory:
        raise HTTPException(status_code=404, detail="Advisory not found")

    linked_servers = [link.server_id for link in session.query(VulnLink).filter(VulnLink.advisory_id == advisory_id).all()]

    confidence_breakdown = {
        "high": session.query(VulnLink).filter(
            VulnLink.advisory_id == advisory_id,
            VulnLink.match_confidence == "high"
        ).count(),
        "medium": session.query(VulnLink).filter(
            VulnLink.advisory_id == advisory_id,
            VulnLink.match_confidence == "medium"
        ).count(),
        "low": session.query(VulnLink).filter(
            VulnLink.advisory_id == advisory_id,
            VulnLink.match_confidence == "low"
        ).count()
    }

    return {
        "id": advisory.id,
        "feed": advisory.feed,
        "summary": advisory.summary,
        "severity": advisory.severity,
        "ecosystem": advisory.ecosystem,
        "package": advisory.package,
        "affected_ranges": advisory.affected_ranges,
        "aliases": advisory.aliases,
        "source_url": advisory.source_url,
        "published_at": advisory.published_at,
        "linked_servers": linked_servers,
        "confidence_breakdown": confidence_breakdown
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override dependency for testing
    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create test data
    def create_test_data():
        session = TestSessionLocal()
        try:
            # Create test server
            server = MCPServerRegistry(id=1, hostname="test-server", ip_address="192.168.1.1")
            session.add(server)

            # Create test advisories
            adv1 = VulnAdvisory(
                id=1,
                feed="test-feed",
                summary="Critical vulnerability in test package",
                severity="CRITICAL",
                ecosystem="python",
                package="test-package",
                affected_ranges=">=1.0.0,<2.0.0",
                aliases="CVE-2023-1234",
                source_url="https://example.com/vuln1",
                published_at=datetime.now()
            )
            adv2 = VulnAdvisory(
                id=2,
                feed="test-feed",
                summary="High vulnerability in another package",
                severity="HIGH",
                ecosystem="javascript",
                package="another-package",
                affected_ranges=">=3.0.0",
                aliases="CVE-2023-5678",
                source_url="https://example.com/vuln2",
                published_at=datetime.now()
            )
            session.add_all([adv1, adv2])

            # Create test links
            link1 = VulnLink(server_id=1, advisory_id=1, match_confidence="high", match_basis="version")
            link2 = VulnLink(server_id=1, advisory_id=2, match_confidence="medium", match_basis="dependency")
            session.add_all([link1, link2])

            session.commit()
        finally:
            session.close()

    create_test_data()

    client = TestClient(app)

    # Test GET /servers/{server_id}/vuln-advisories
    response = client.get("/servers/1/vuln-advisories")
    assert response.status_code == 200
    assert len(response.json()["advisories"]) == 2

    # Test GET /vuln-advisories/search?q=CRITICAL
    response = client.get("/vuln-advisories/search?q=CRITICAL")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    assert response.json()["results"][0]["severity"] == "CRITICAL"

    # Test GET /vuln-advisories/{advisory_id}
    response = client.get("/vuln-advisories/1")
    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert len(response.json()["linked_servers"]) == 1
    assert "confidence_breakdown" in response.json()

    print("PASS")