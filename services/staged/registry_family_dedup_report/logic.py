from typing import List, Dict, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from app.db import get_session
from app.models import McpServerRegistry
from Levenshtein import distance as levenshtein_distance
from fastapi import Depends

class ServerMember(BaseModel):
    server_id: str
    name: str
    url: str
    last_seen: Optional[str]

class ServerFamily(BaseModel):
    canonical_id: str
    name: str
    members: List[ServerMember]

class RegistryFamilyDedupReport(BaseModel):
    total_servers: int
    families: List[ServerFamily]

def get_registry_family_dedup_report(db: Session = Depends(get_session)) -> RegistryFamilyDedupReport:
    # Query all servers from the registry
    servers = db.query(McpServerRegistry).all()

    # Group servers by URL domain prefix (e.g., github.com/org)
    domain_groups = {}
    for server in servers:
        if not server.url:
            continue
        domain = '/'.join(server.url.split('/')[:3])
        if domain not in domain_groups:
            domain_groups[domain] = []
        domain_groups[domain].append(server)

    # Process each domain group to find families
    families = []
    processed_ids = set()

    for domain, group in domain_groups.items():
        if len(group) < 2:
            continue

        # Sort by last_seen to prioritize more recent servers
        group.sort(key=lambda x: x.last_seen, reverse=True)

        # Use the first server as the canonical for this domain
        canonical = group[0]
        family_members = [canonical]
        processed_ids.add(canonical.server_id)

        # Compare other servers in the domain to the canonical
        for server in group[1:]:
            if server.server_id in processed_ids:
                continue

            # Simple heuristic: if names are similar (Levenshtein < 5) and share domain
            if (levenshtein_distance(server.name.lower(), canonical.name.lower()) < 5 and
                server.url.startswith(canonical.url.split('/')[0])):
                family_members.append(server)
                processed_ids.add(server.server_id)

        if len(family_members) > 1:
            families.append({
                "canonical_id": canonical.server_id,
                "name": canonical.name,
                "members": [{
                    "server_id": m.server_id,
                    "name": m.name,
                    "url": m.url,
                    "last_seen": m.last_seen
                } for m in family_members]
            })

    # Add remaining ungrouped servers as their own families
    for server in servers:
        if server.server_id not in processed_ids:
            families.append({
                "canonical_id": server.server_id,
                "name": server.name,
                "members": [{
                    "server_id": server.server_id,
                    "name": server.name,
                    "url": server.url,
                    "last_seen": server.last_seen
                }]
            })

    return RegistryFamilyDedupReport(
        total_servers=len(servers),
        families=families
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from datetime import datetime

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    test_servers = [
        McpServerRegistry(
            server_id="github.com/org/repo1",
            name="Repo 1",
            url="https://github.com/org/repo1",
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="github.com/org/repo2",
            name="Repo 2",
            url="https://github.com/org/repo2",
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="github.com/org/repo-extra",
            name="Repo Extra",
            url="https://github.com/org/repo-extra",
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="gitlab.com/other/repo1",
            name="Other Repo 1",
            url="https://gitlab.com/other/repo1",
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="gitlab.com/other/repo2",
            name="Other Repo 2",
            url="https://gitlab.com/other/repo2",
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="bitbucket.org/team/project",
            name="Team Project",
            url="https://bitbucket.org/team/project",
            last_seen=datetime.now()
        )
    ]

    db = SessionLocal()
    db.add_all(test_servers)
    db.commit()

    # Run the report
    report = get_registry_family_dedup_report()

    # Assertions
    assert report.total_servers == 6
    assert len(report.families) == 3

    family_sizes = [len(family.members) for family in report.families]
    assert 2 in family_sizes

    print("PASS")