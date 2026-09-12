from typing import List, Dict, Any
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from pydantic import BaseModel
from fuzzywuzzy import fuzz

class Server(BaseModel):
    server_id: str
    name: str
    first_seen: str

class Family(BaseModel):
    family_id: str
    registry_source: str
    count: int
    servers: List[Server]

class Report(BaseModel):
    families: List[Family]

def detect_duplicate_families(db: Session = Depends(get_session)) -> Report:
    servers = db.query(McpServerRegistry).all()

    # Group servers by registry_source
    source_groups = {}
    for server in servers:
        if server.registry_source not in source_groups:
            source_groups[server.registry_source] = []
        source_groups[server.registry_source].append(server)

    families = []
    family_id = 1

    for source, servers in source_groups.items():
        # Group servers into families based on name similarity
        ungrouped_servers = servers.copy()
        while ungrouped_servers:
            # Start a new family with the first ungrouped server
            family_servers = [ungrouped_servers.pop(0)]
            family_servers_to_remove = []

            # Compare with remaining ungrouped servers
            for i, server in enumerate(ungrouped_servers):
                for family_server in family_servers:
                    # Check for similarity (Levenshtein ratio > 0.85 or same org prefix)
                    if (fuzz.ratio(server.name.lower(), family_server.name.lower()) > 85 or
                        server.name.split('.')[0] == family_server.name.split('.')[0]):
                        family_servers.append(server)
                        family_servers_to_remove.append(i)
                        break

            # Remove grouped servers from ungrouped list
            for idx in sorted(family_servers_to_remove, reverse=True):
                ungrouped_servers.pop(idx)

            # Create family record
            family = Family(
                family_id=str(family_id),
                registry_source=source,
                count=len(family_servers),
                servers=[Server(
                    server_id=server.server_id,
                    name=server.name,
                    first_seen=str(server.first_seen)
                ) for server in family_servers]
            )
            families.append(family)
            family_id += 1

    return Report(families=families)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_db)
    TestSession = sessionmaker(bind=test_db)

    # Create test app
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test route
    @test_app.get("/api/registry/family/dedup-report")
    async def test_route(db: Session = Depends(get_session)):
        return detect_duplicate_families(db)

    # Create test data
    test_servers = [
        McpServerRegistry(
            server_id="1",
            name="server1.example.com",
            registry_source="source1",
            first_seen="2023-01-01",
            confidence=0.9,
            description="Test server 1",
            last_assessed="2023-01-02",
            last_scanned="2023-01-02",
            last_seen="2023-01-03",
            meta={},
            risk_tier="low",
            scan_count=1,
            trust_score=0.8,
            url="http://server1.example.com",
            verdict="safe",
            verdict_reasoning="Test reasoning"
        ),
        McpServerRegistry(
            server_id="2",
            name="server2.example.com",
            registry_source="source1",
            first_seen="2023-01-01",
            confidence=0.9,
            description="Test server 2",
            last_assessed="2023-01-02",
            last_scanned="2023-01-02",
            last_seen="2023-01-03",
            meta={},
            risk_tier="low",
            scan_count=1,
            trust_score=0.8,
            url="http://server2.example.com",
            verdict="safe",
            verdict_reasoning="Test reasoning"
        ),
        McpServerRegistry(
            server_id="3",
            name="server1.example.com",  # Duplicate of server1
            registry_source="source1",
            first_seen="2023-01-01",
            confidence=0.9,
            description="Test server 3",
            last_assessed="2023-01-02",
            last_scanned="2023-01-02",
            last_seen="2023-01-03",
            meta={},
            risk_tier="low",
            scan_count=1,
            trust_score=0.8,
            url="http://server1.example.com",
            verdict="safe",
            verdict_reasoning="Test reasoning"
        ),
        McpServerRegistry(
            server_id="4",
            name="server3.example.com",
            registry_source="source2",
            first_seen="2023-01-01",
            confidence=0.9,
            description="Test server 4",
            last_assessed="2023-01-02",
            last_scanned="2023-01-02",
            last_seen="2023-01-03",
            meta={},
            risk_tier="low",
            scan_count=1,
            trust_score=0.8,
            url="http://server3.example.com",
            verdict="safe",
            verdict_reasoning="Test reasoning"
        ),
        McpServerRegistry(
            server_id="5",
            name="server4.example.com",
            registry_source="source2",
            first_seen="2023-01-01",
            confidence=0.9,
            description="Test server 5",
            last_assessed="2023-01-02",
            last_scanned="2023-01-02",
            last_seen="2023-01-03",
            meta={},
            risk_tier="low",
            scan_count=1,
            trust_score=0.8,
            url="http://server4.example.com",
            verdict="safe",
            verdict_reasoning="Test reasoning"
        )
    ]

    # Add test data to database
    test_session = TestSession()
    for server in test_servers:
        test_session.add(server)
    test_session.commit()

    # Run test
    client = TestClient(test_app)
    response = client.get("/api/registry/family/dedup-report")
    assert response.status_code == 200

    # Verify response
    data = response.json()
    assert len(data["families"]) == 3  # 2 from source1 (1 family with 2 servers), 2 from source2 (2 families)

    # Check source1 family
    source1_family = next(f for f in data["families"] if f["registry_source"] == "source1")
    assert source1_family["count"] == 2
    assert len(source1_family["servers"]) == 2
    assert any(s["name"] == "server1.example.com" for s in source1_family["servers"])

    # Check source2 families
    source2_families = [f for f in data["families"] if f["registry_source"] == "source2"]
    assert len(source2_families) == 2
    for family in source2_families:
        assert family["count"] == 1
        assert len(family["servers"]) == 1

    print("PASS")