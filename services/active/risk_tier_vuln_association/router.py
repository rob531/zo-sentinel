# deps: fastapi, requests
"""risk_tier_vuln_association — API for viewing risk-tier to vulnerability associations.

Provides HTTP endpoints that expose the relationship between server risk tiers
and their associated vulnerability data.

GET  /api/associations                    List all risk-tier to vuln associations.
GET  /api/associations/{server_id}        Get associations for a specific server.
GET  /api/associations/tier/{risk_tier}   Get all servers and their vulns by tier.
GET  /api/summary                         Aggregate vuln counts by risk tier.

Auth: public.
Data: app tier via get_session + McpServerRegistry + VulnLink + VulnAdvisory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnLink, VulnAdvisory

router = APIRouter(prefix="/api", tags=["risk_tier_vuln_association"])


# --------------------------------------------------------------------------- #
# Request / response shapes
# --------------------------------------------------------------------------- #

class VulnSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    advisory_id: int
    summary: str
    severity: Optional[str] = None
    package: Optional[str] = None
    feed: Optional[str] = None


class ServerVulnAssociation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    server_name: Optional[str] = None
    risk_tier: Optional[str] = None
    vuln_count: int = 0
    vulns: list[VulnSummary] = []


class TierVulnSummary(BaseModel):
    risk_tier: str
    server_count: int
    total_vulns: int
    high_severity_count: int


class AssociationListResponse(BaseModel):
    associations: list[ServerVulnAssociation]
    total: int


class SummaryResponse(BaseModel):
    tiers: list[TierVulnSummary]
    fetched_at: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _get_vulns_for_server(session: Session, server_id: str) -> list[VulnSummary]:
    """Fetch vulnerability summaries linked to a server."""
    query = (
        select(
            VulnLink.advisory_id,
            VulnAdvisory.summary,
            VulnAdvisory.severity,
            VulnAdvisory.package,
            VulnAdvisory.feed,
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .where(VulnLink.server_id == server_id)
        .limit(100)
    )
    results = session.execute(query).all()
    return [
        VulnSummary(
            advisory_id=r.advisory_id,
            summary=r.summary,
            severity=r.severity,
            package=r.package,
            feed=r.feed,
        )
        for r in results
    ]


def _build_association(session: Session, server: McpServerRegistry) -> ServerVulnAssociation:
    """Build a full association object for a server."""
    vulns = _get_vulns_for_server(session, server.server_id)
    return ServerVulnAssociation(
        server_id=server.server_id,
        server_name=server.name,
        risk_tier=server.risk_tier,
        vuln_count=len(vulns),
        vulns=vulns,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/associations", response_model=AssociationListResponse)
def list_associations(
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    risk_tier: Optional[str] = Query(default=None),
) -> AssociationListResponse:
    """List all server-vuln associations, optionally filtered by risk tier."""
    query = select(McpServerRegistry)
    
    if risk_tier:
        query = query.where(McpServerRegistry.risk_tier == risk_tier)
    
    query = query.order_by(McpServerRegistry.risk_tier).offset(offset).limit(limit)
    servers = session.execute(query).scalars().all()
    
    associations = [_build_association(session, s) for s in servers]
    
    # Get total count
    count_query = select(func.count(McpServerRegistry.server_id))
    if risk_tier:
        count_query = count_query.where(McpServerRegistry.risk_tier == risk_tier)
    total = session.execute(count_query).scalar() or 0
    
    return AssociationListResponse(associations=associations, total=total)


@router.get("/associations/{server_id}", response_model=ServerVulnAssociation)
def get_server_association(
    server_id: str,
    session: Session = Depends(get_session),
) -> ServerVulnAssociation:
    """Get vulnerability associations for a specific server."""
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    return _build_association(session, server)


@router.get("/associations/tier/{risk_tier}", response_model=AssociationListResponse)
def get_associations_by_tier(
    risk_tier: str,
    session: Session = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AssociationListResponse:
    """Get all servers and their vulnerabilities for a specific risk tier."""
    query = (
        select(McpServerRegistry)
        .where(McpServerRegistry.risk_tier == risk_tier)
        .order_by(McpServerRegistry.name)
        .offset(offset)
        .limit(limit)
    )
    servers = session.execute(query).scalars().all()
    
    associations = [_build_association(session, s) for s in servers]
    
    count_query = select(func.count(McpServerRegistry.server_id)).where(
        McpServerRegistry.risk_tier == risk_tier
    )
    total = session.execute(count_query).scalar() or 0
    
    return AssociationListResponse(associations=associations, total=total)


@router.get("/summary", response_model=SummaryResponse)
def get_vuln_summary_by_tier(
    session: Session = Depends(get_session),
) -> SummaryResponse:
    """Aggregate vulnerability counts by risk tier."""
    # Get counts by tier
    tier_counts = {}
    all_servers = session.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
    ).all()
    
    for server_id, risk_tier in all_servers:
        tier = risk_tier or "unknown"
        if tier not in tier_counts:
            tier_counts[tier] = {"server_count": 0, "server_ids": set(), "vuln_ids": set()}
        tier_counts[tier]["server_count"] += 1
        tier_counts[tier]["server_ids"].add(server_id)
    
    # Get vuln links and join with servers
    vuln_links = session.execute(
        select(VulnLink.server_id, VulnLink.advisory_id, VulnAdvisory.severity)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
    ).all()
    
    for server_id, advisory_id, severity in vuln_links:
        for tier_data in tier_counts.values():
            if server_id in tier_data["server_ids"]:
                tier_data["vuln_ids"].add(advisory_id)
    
    # Build response
    tiers = []
    for risk_tier, data in sorted(tier_counts.items()):
        total_vulns = len(data["vuln_ids"])
        # Count high severity
        high_sev_count = session.execute(
            select(func.count(VulnAdvisory.id))
            .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
            .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
            .where(
                McpServerRegistry.risk_tier == risk_tier,
                VulnAdvisory.severity.in_(["critical", "high"]),
            )
        ).scalar() or 0
        
        tiers.append(TierVulnSummary(
            risk_tier=risk_tier,
            server_count=data["server_count"],
            total_vulns=total_vulns,
            high_severity_count=high_sev_count,
        ))
    
    return SummaryResponse(
        tiers=tiers,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock, patch

    # Test imports
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.models import Base
    except ModuleNotFoundError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    # Build test app
    app = FastAPI()
    app.include_router(router)

    # Create test engine
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Seed test data
    db = TestSessionLocal()
    server1 = McpServerRegistry(
        server_id="srv-001",
        name="Test Server Alpha",
        risk_tier="high",
        registry_source="test",
    )
    server2 = McpServerRegistry(
        server_id="srv-002",
        name="Test Server Beta",
        risk_tier="low",
        registry_source="test",
    )
    db.add_all([server1, server2])

    advisory1 = VulnAdvisory(
        id=1,
        feed="test-feed",
        summary="Critical vulnerability in libfoo",
        severity="critical",
        ecosystem="npm",
        package="libfoo",
    )
    advisory2 = VulnAdvisory(
        id=2,
        feed="test-feed",
        summary="Low severity issue in libbar",
        severity="low",
        ecosystem="pip",
        package="libbar",
    )
    db.add_all([advisory1, advisory2])

    link1 = VulnLink(
        advisory_id=1,
        server_id="srv-001",
        match_basis="package",
        match_value="libfoo",
        match_confidence=0.95,
    )
    link2 = VulnLink(
        advisory_id=2,
        server_id="srv-002",
        match_basis="package",
        match_value="libbar",
        match_confidence=0.80,
    )
    db.add_all([link1, link2])
    db.commit()
    db.close()

    # Override dependency
    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from app.main import app as main_app
    main_app.dependency_overrides[get_session] = _override

    client = TestClient(main_app)

    # Test 1: List associations
    response = client.get("/api/associations")
    assert response.status_code == 200, f"List failed: {response.status_code}"
    data = response.json()
    assert "associations" in data
    assert data["total"] >= 2

    # Test 2: Get specific server
    response = client.get("/api/associations/srv-001")
    assert response.status_code == 200, f"Server lookup failed: {response.status_code}"
    srv_data = response.json()
    assert srv_data["server_id"] == "srv-001"
    assert srv_data["risk_tier"] == "high"

    # Test 3: Get by tier
    response = client.get("/api/associations/tier/high")
    assert response.status_code == 200, f"Tier lookup failed: {response.status_code}"
    tier_data = response.json()
    assert all(a["risk_tier"] == "high" for a in tier_data["associations"])

    # Test 4: Summary
    response = client.get("/api/summary")
    assert response.status_code == 200, f"Summary failed: {response.status_code}"
    summary = response.json()
    assert "tiers" in summary
    assert "fetched_at" in summary

    main_app.dependency_overrides.clear()
    print("PASS")
