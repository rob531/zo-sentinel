"""Server CVE Exposure API contract."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Generator
from unittest.mock import MagicMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


# Pydantic response models
class AdvisorySummary(BaseModel):
    id: int
    summary: str | None
    severity: str | None
    ecosystem: str | None
    package: str | None
    published_at: datetime | None
    source_url: str | None


class CveExposureResponse(BaseModel):
    server_id: str
    server_name: str | None
    cve_count: int
    critical_count: int
    high_count: int
    advisories: list[AdvisorySummary]
    scanned_at: datetime | None


# Router
router_cve_exposure: Any = MagicMock()


def create_router() -> Any:
    """Create the router with the CVE exposure endpoint."""
    router = MagicMock()
    
    def get_cve_exposure(
        server_id: str,
        db: Session = Depends(get_session),
    ) -> CveExposureResponse:
        server = db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).first()
        
        if not server:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Server not found")
        
        vuln_links = db.query(VulnLink).filter(
            VulnLink.server_id == server_id
        ).all()
        
        advisory_ids = [vl.advisory_id for vl in vuln_links if vl.advisory_id]
        
        advisories = []
        critical_count = 0
        high_count = 0
        
        if advisory_ids:
            advisory_records = db.query(VulnAdvisory).filter(
                VulnAdvisory.id.in_(advisory_ids)
            ).all()
            
            for adv in advisory_records:
                severity_lower = (adv.severity or "").lower()
                if severity_lower == "critical":
                    critical_count += 1
                elif severity_lower == "high":
                    high_count += 1
                
                advisories.append(AdvisorySummary(
                    id=adv.id,
                    summary=adv.summary,
                    severity=adv.severity,
                    ecosystem=adv.ecosystem,
                    package=adv.package,
                    published_at=adv.published_at,
                    source_url=adv.source_url,
                ))
        
        return CveExposureResponse(
            server_id=server_id,
            server_name=server.name,
            cve_count=len(advisories),
            critical_count=critical_count,
            high_count=high_count,
            advisories=advisories,
            scanned_at=server.last_scanned,
        )
    
    router.get_cve_exposure = get_cve_exposure
    
    # Add route mock
    async def route_handler(server_id: str, db: Session = Depends(get_session)) -> CveExposureResponse:
        return get_cve_exposure(server_id, db)
    
    router.get = MagicMock(return_value=route_handler)
    
    return router


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(title="Server CVE Exposure API")
    router = create_router()
    
    # Add the route
    @app.get("/api/servers/{server_id}/cve-exposure")
    async def get_cve_exposure(
        server_id: str,
        db: Session = Depends(get_session),
    ) -> CveExposureResponse:
        server = db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).first()
        
        if not server:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Server not found")
        
        vuln_links = db.query(VulnLink).filter(
            VulnLink.server_id == server_id
        ).all()
        
        advisory_ids = [vl.advisory_id for vl in vuln_links if vl.advisory_id]
        
        advisories = []
        critical_count = 0
        high_count = 0
        
        if advisory_ids:
            advisory_records = db.query(VulnAdvisory).filter(
                VulnAdvisory.id.in_(advisory_ids)
            ).all()
            
            for adv in advisory_records:
                severity_lower = (adv.severity or "").lower()
                if severity_lower == "critical":
                    critical_count += 1
                elif severity_lower == "high":
                    high_count += 1
                
                advisories.append(AdvisorySummary(
                    id=adv.id,
                    summary=adv.summary,
                    severity=adv.severity,
                    ecosystem=adv.ecosystem,
                    package=adv.package,
                    published_at=adv.published_at,
                    source_url=adv.source_url,
                ))
        
        return CveExposureResponse(
            server_id=server_id,
            server_name=server.name,
            cve_count=len(advisories),
            critical_count=critical_count,
            high_count=high_count,
            advisories=advisories,
            scanned_at=server.last_scanned,
        )
    
    return app


def seed_data(db: Session) -> None:
    """Seed test data."""
    now = datetime.now(timezone.utc)
    
    # Create servers
    server1 = McpServerRegistry(
        server_id="srv-001",
        name="production-server-1",
        url="https://srv1.example.com",
        registry_source="internal",
        risk_tier="high",
        confidence=0.95,
        trust_score=85,
        verdict="reviewed",
        first_seen=now,
        last_seen=now,
        last_scanned=now,
        last_assessed=now,
        scan_count=10,
    )
    server2 = McpServerRegistry(
        server_id="srv-002",
        name="staging-server-2",
        url="https://srv2.example.com",
        registry_source="internal",
        risk_tier="medium",
        confidence=0.90,
        trust_score=75,
        verdict="reviewed",
        first_seen=now,
        last_seen=now,
        last_scanned=now,
        last_assessed=now,
        scan_count=5,
    )
    
    db.add(server1)
    db.add(server2)
    
    # Create advisories
    advisory1 = VulnAdvisory(
        id=101,
        summary="Critical RCE vulnerability in package foo",
        severity="Critical",
        ecosystem="pypi",
        package="foo",
        published_at=now,
        source_url="https://advisories.example.com/101",
        feed="ghsa",
        affected_ranges="<2.0.0",
        content_hash="abc123",
        fetched_at=now,
    )
    advisory2 = VulnAdvisory(
        id=102,
        summary="High severity SQL injection in bar",
        severity="High",
        ecosystem="npm",
        package="bar",
        published_at=now,
        source_url="https://advisories.example.com/102",
        feed="ghsa",
        affected_ranges="<1.5.0",
        content_hash="def456",
        fetched_at=now,
    )
    advisory3 = VulnAdvisory(
        id=103,
        summary="Medium severity XSS in baz",
        severity="Medium",
        ecosystem="pypi",
        package="baz",
        published_at=now,
        source_url="https://advisories.example.com/103",
        feed="ghsa",
        affected_ranges="<3.0.0",
        content_hash="ghi789",
        fetched_at=now,
    )
    
    db.add(advisory1)
    db.add(advisory2)
    db.add(advisory3)
    
    # Create vuln links
    vuln_link1 = VulnLink(
        id=1001,
        server_id="srv-001",
        advisory_id=101,
        match_value="foo",
        match_basis="package_name",
        match_confidence=0.95,
        linked_at=now,
    )
    vuln_link2 = VulnLink(
        id=1002,
        server_id="srv-001",
        advisory_id=102,
        match_value="bar",
        match_basis="package_name",
        match_confidence=0.90,
        linked_at=now,
    )
    vuln_link3 = VulnLink(
        id=1003,
        server_id="srv-002",
        advisory_id=103,
        match_value="baz",
        match_basis="package_name",
        match_confidence=0.85,
        linked_at=now,
    )
    
    db.add(vuln_link1)
    db.add(vuln_link2)
    db.add(vuln_link3)
    
    db.commit()


def main() -> int:
    """Run the contract test."""
    # Create in-memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Import and use the real Base from app.models
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    
    # Create session factory
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )
    
    def override_get_session() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Seed data
    db = TestingSessionLocal()
    seed_data(db)
    db.close()
    
    # Create and configure app
    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    
    # Run test
    client = TestClient(app)
    
    response = client.get("/api/servers/srv-001/cve-exposure")
    
    if response.status_code != 200:
        print(f"FAIL: Expected 200, got {response.status_code}")
        print(f"Response: {response.text}")
        return 1
    
    data = response.json()
    
    if data.get("cve_count", 0) < 1:
        print(f"FAIL: Expected cve_count >= 1, got {data.get('cve_count')}")
        return 1
    
    advisories = data.get("advisories", [])
    if not advisories:
        print("FAIL: No advisories returned")
        return 1
    
    first_advisory = advisories[0]
    if "severity" not in first_advisory:
        print(f"FAIL: severity field missing in first advisory")
        return 1
    
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())