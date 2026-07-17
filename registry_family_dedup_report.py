from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry, Org
from sqlalchemy.orm import Session
from collections import defaultdict
import re

router = APIRouter()

class ServerFamily(BaseModel):
    canonical_server_id: int
    variants: List[int]

class RegistryFamilyDedupReport(BaseModel):
    families: List[ServerFamily]
    total_servers: int
    total_families: int

def _normalize_name(name: str) -> str:
    """Normalize server names by removing common prefixes/suffixes and special characters."""
    name = name.lower()
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\b(the|a|an)\b', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def _get_server_families(db: Session) -> List[ServerFamily]:
    """Identify server families based on name similarity and shared org/registry source."""
    servers = db.query(MCPServerRegistry).all()
    orgs = db.query(Org).all()
    org_map = {org.id: org.name for org in orgs}

    # Group by org and registry source
    groups = defaultdict(list)
    for server in servers:
        org_name = org_map.get(server.org_id, "Unknown")
        key = (org_name, server.registry_source)
        groups[key].append(server)

    families = []
    for group in groups.values():
        if len(group) < 2:
            continue

        # Find canonical server (longest name)
        canonical = max(group, key=lambda x: len(x.name))
        canonical_id = canonical.id

        # Find variants (similar names)
        variants = []
        for server in group:
            if server.id == canonical_id:
                continue
            if _normalize_name(server.name) == _normalize_name(canonical.name):
                variants.append(server.id)

        if variants:
            families.append(ServerFamily(
                canonical_server_id=canonical_id,
                variants=variants
            ))

    return families

@router.get("/reports/registry-family-dedup", response_model=RegistryFamilyDedupReport)
async def get_registry_family_dedup_report(db: Session = Depends(get_session)) -> RegistryFamilyDedupReport:
    families = _get_server_families(db)
    total_servers = len([server for family in families for server in [family.canonical_server_id] + family.variants])
    total_families = len(families)

    return RegistryFamilyDedupReport(
        families=families,
        total_servers=total_servers,
        total_families=total_families
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_org1 = Org(name="Test Org 1")
    test_org2 = Org(name="Test Org 2")
    test_session.add_all([test_org1, test_org2])
    test_session.commit()

    test_servers = [
        MCPServerRegistry(
            name="Test Server 1",
            registry_source="source1",
            url="http://example.com/1",
            org_id=test_org1.id
        ),
        MCPServerRegistry(
            name="The Test Server 1",
            registry_source="source1",
            url="http://example.com/2",
            org_id=test_org1.id
        ),
        MCPServerRegistry(
            name="Test Server 2",
            registry_source="source2",
            url="http://example.com/3",
            org_id=test_org2.id
        ),
        MCPServerRegistry(
            name="Another Test Server",
            registry_source="source1",
            url="http://example.com/4",
            org_id=test_org1.id
        ),
    ]
    test_session.add_all(test_servers)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/reports/registry-family-dedup")
    assert response.status_code == 200
    report = response.json()

    assert report["total_servers"] > 0
    assert report["total_families"] > 0
    assert len(report["families"]) > 0
    assert all("canonical_server_id" in family for family in report["families"])
    assert all("variants" in family for family in report["families"])

    print("PASS")