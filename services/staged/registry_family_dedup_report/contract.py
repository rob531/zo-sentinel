from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func
from Levenshtein import distance as levenshtein_distance
from urllib.parse import urlparse

router = APIRouter(prefix="/api")

class ServerMember(BaseModel):
    server_id: str
    name: str
    url: str
    last_seen: str

class ServerFamily(BaseModel):
    canonical_id: str
    name: str
    members: List[ServerMember]

class RegistryFamilyDedupReport(BaseModel):
    total_servers: int
    families: List[ServerFamily]

def get_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except:
        return ""

def group_servers_by_family(db: Session) -> RegistryFamilyDedupReport:
    servers = db.query(McpServerRegistry).all()

    # Group by domain and name similarity
    domain_groups = {}
    for server in servers:
        domain = get_domain(server.url)
        if domain not in domain_groups:
            domain_groups[domain] = []
        domain_groups[domain].append(server)

    families = []
    for domain, servers in domain_groups.items():
        if len(servers) > 1:
            # Find canonical server (shortest name)
            canonical = min(servers, key=lambda x: len(x.name))
            family = {
                "canonical_id": canonical.server_id,
                "name": canonical.name,
                "members": []
            }

            for server in servers:
                if levenshtein_distance(server.name, canonical.name) <= 5:
                    family["members"].append({
                        "server_id": server.server_id,
                        "name": server.name,
                        "url": server.url,
                        "last_seen": server.last_seen.isoformat() if server.last_seen else None
                    })

            if len(family["members"]) > 1:
                families.append(family)

    return RegistryFamilyDedupReport(
        total_servers=len(servers),
        families=families
    )

@router.get("/reports/registry-family-dedup", response_model=RegistryFamilyDedupReport)
async def get_registry_family_dedup_report(db: Session = Depends(get_session)):
    return group_servers_by_family(db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app = FastAPI()
    app.include_router(router)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        test_servers = [
            McpServerRegistry(
                server_id="1",
                name="github.com/org/repo",
                url="https://github.com/org/repo",
                last_seen=None
            ),
            McpServerRegistry(
                server_id="2",
                name="github.com/org/repo-extra",
                url="https://github.com/org/repo-extra",
                last_seen=None
            ),
            McpServerRegistry(
                server_id="3",
                name="gitlab.com/other/repo",
                url="https://gitlab.com/other/repo",
                last_seen=None
            ),
            McpServerRegistry(
                server_id="4",
                name="gitlab.com/other/repo2",
                url="https://gitlab.com/other/repo2",
                last_seen=None
            ),
            McpServerRegistry(
                server_id="5",
                name="bitbucket.org/another/repo",
                url="https://bitbucket.org/another/repo",
                last_seen=None
            ),
            McpServerRegistry(
                server_id="6",
                name="bitbucket.org/another/repo-extra",
                url="https://bitbucket.org/another/repo-extra",
                last_seen=None
            )
        ]
        session.add_all(test_servers)
        session.commit()

    client = TestClient(app)

    # Test endpoint
    response = client.get("/api/reports/registry-family-dedup")
    assert response.status_code == 200
    data = response.json()

    assert data["total_servers"] == 6
    assert len(data["families"]) == 3

    # Check one family has 2 members
    family_found = False
    for family in data["families"]:
        if len(family["members"]) == 2:
            family_found = True
            break
    assert family_found

    print("PASS")