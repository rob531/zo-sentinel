from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink
from sqlalchemy import func, and_, or_
from sqlalchemy.pool import StaticPool
import sqlite3
from contextlib import asynccontextmanager

class CVESummary(BaseModel):
    id: str
    severity: str
    summary: str

class FamilyHierarchy(BaseModel):
    family_key: str
    source: str
    server_count: int
    avg_trust_score: float
    risk_tiers: Dict[str, int]
    cve_count: int
    top_cves: List[CVESummary]

class FamilyHierarchyResponse(BaseModel):
    families: List[FamilyHierarchy]

def get_family_key(server: McpServerRegistry) -> str:
    if server.registry_source:
        return server.registry_source
    if server.url:
        return server.url.split('/')[-1]
    return server.name.split('/')[-1]

def get_package_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    parts = url.split('/')
    if len(parts) >= 2:
        return parts[-2]
    return None

def get_package_from_name(name: str) -> Optional[str]:
    if not name:
        return None
    parts = name.split('/')
    if len(parts) >= 2:
        return parts[-2]
    return None

def get_family_hierarchy(db: Session) -> FamilyHierarchyResponse:
    # Get all servers with their vulnerabilities
    servers = db.query(
        McpServerRegistry,
        func.count(VulnLink.id).label('cve_count')
    ).outerjoin(
        VulnLink, McpServerRegistry.server_id == VulnLink.server_id
    ).group_by(
        McpServerRegistry.server_id
    ).all()

    # Group servers by family
    families = {}
    for server, cve_count in servers:
        family_key = get_family_key(server)
        package = get_package_from_url(server.url) or get_package_from_name(server.name)

        if family_key not in families:
            families[family_key] = {
                'source': server.registry_source or 'unknown',
                'servers': [],
                'total_cve_count': 0,
                'risk_tiers': {}
            }

        families[family_key]['servers'].append(server)
        families[family_key]['total_cve_count'] += cve_count

        # Count risk tiers
        risk_tier = server.risk_tier or 'unknown'
        families[family_key]['risk_tiers'][risk_tier] = families[family_key]['risk_tiers'].get(risk_tier, 0) + 1

    # Prepare response
    response_families = []
    for family_key, family_data in families.items():
        servers = family_data['servers']
        avg_trust_score = sum(s.trust_score for s in servers) / len(servers) if servers else 0

        # Get top CVEs for this family
        top_cves = db.query(
            VulnAdvisory.id,
            VulnAdvisory.severity,
            VulnAdvisory.summary
        ).join(
            VulnLink, VulnAdvisory.id == VulnLink.advisory_id
        ).join(
            McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id
        ).filter(
            get_family_key(McpServerRegistry) == family_key
        ).order_by(
            VulnAdvisory.severity.desc()
        ).limit(5).all()

        response_families.append(FamilyHierarchy(
            family_key=family_key,
            source=family_data['source'],
            server_count=len(servers),
            avg_trust_score=avg_trust_score,
            risk_tiers=family_data['risk_tiers'],
            cve_count=family_data['total_cve_count'],
            top_cves=[CVESummary(id=cve.id, severity=cve.severity, summary=cve.summary) for cve in top_cves]
        ))

    return FamilyHierarchyResponse(families=response_families)

app = FastAPI()

@app.get("/api/registry/family-hierarchy", response_model=FamilyHierarchyResponse)
async def get_family_hierarchy_endpoint(db: Session = Depends(get_session)):
    return get_family_hierarchy(db)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create in-memory SQLite database for testing
    engine = sqlite3.connect(":memory:", check_same_thread=False)
    engine.execute("PRAGMA foreign_keys = ON")
    engine.execute("""
        CREATE TABLE McpServerRegistry (
            server_id INTEGER PRIMARY KEY,
            name TEXT,
            registry_source TEXT,
            url TEXT,
            description TEXT,
            trust_score REAL,
            verdict TEXT,
            confidence REAL,
            last_assessed TEXT,
            first_seen TEXT,
            last_seen TEXT,
            risk_tier TEXT,
            meta TEXT
        )
    """)
    engine.execute("""
        CREATE TABLE vuln_advisories (
            id TEXT PRIMARY KEY,
            feed TEXT,
            summary TEXT,
            severity TEXT,
            ecosystem TEXT,
            package TEXT,
            affected_ranges TEXT,
            aliases TEXT
        )
    """)
    engine.execute("""
        CREATE TABLE vuln_links (
            id INTEGER PRIMARY KEY,
            server_id INTEGER,
            advisory_id TEXT,
            match_basis TEXT,
            match_confidence REAL,
            FOREIGN KEY(server_id) REFERENCES McpServerRegistry(server_id),
            FOREIGN KEY(advisory_id) REFERENCES vuln_advisories(id)
        )
    """)

    # Insert test data
    engine.execute("""
        INSERT INTO McpServerRegistry (server_id, name, registry_source, url, description, trust_score, verdict, confidence, last_assessed, first_seen, last_seen, risk_tier, meta)
        VALUES
            (1, 'server1', 'source1', 'https://source1.com/server1', 'Server 1', 0.9, 'safe', 0.95, '2023-01-01', '2023-01-01', '2023-01-01', 'low', '{}'),
            (2, 'server2', 'source1', 'https://source1.com/server2', 'Server 2', 0.8, 'safe', 0.9, '2023-01-01', '2023-01-01', '2023-01-01', 'medium', '{}'),
            (3, 'server3', 'source2', 'https://source2.com/server3', 'Server 3', 0.7, 'safe', 0.85, '2023-01-01', '2023-01-01', '2023-01-01', 'high', '{}'),
            (4, 'server4', 'source2', 'https://source2.com/server4', 'Server 4', 0.6, 'safe', 0.8, '2023-01-01', '2023-01-01', '2023-01-01', 'low', '{}')
    """)
    engine.execute("""
        INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, affected_ranges, aliases)
        VALUES
            ('CVE-2023-0001', 'feed1', 'Summary 1', 'high', 'ecosystem1', 'package1', 'range1', 'alias1'),
            ('CVE-2023-0002', 'feed1', 'Summary 2', 'medium', 'ecosystem1', 'package1', 'range2', 'alias2'),
            ('CVE-2023-0003', 'feed2', 'Summary 3', 'low', 'ecosystem2', 'package2', 'range3', 'alias3')
    """)
    engine.execute("""
        INSERT INTO vuln_links (server_id, advisory_id, match_basis, match_confidence)
        VALUES
            (1, 'CVE-2023-0001', 'basis1', 0.9),
            (1, 'CVE-2023-0002', 'basis2', 0.8),
            (2, 'CVE-2023-0003', 'basis3', 0.7),
            (3, 'CVE-2023-0001', 'basis4', 0.9),
            (4, 'CVE-2023-0002', 'basis5', 0.8)
    """)

    # Create a session with StaticPool
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    yield

    # Clean up
    app.dependency_overrides[get_session] = get_session
    engine.close()

app.lifespan = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

    # Run self-test
    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/registry/family-hierarchy")
    assert response.status_code == 200
    data = response.json()
    assert len(data['families']) >= 2
    assert sum(family['server_count'] for family in data['families']) >= 4

    print("PASS")