from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import and_, or_
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class ServerSearchResult(BaseModel):
    server_id: str
    name: str
    registry_source: str
    url: str
    description: Optional[str]
    risk_tier: str
    last_assessed: Optional[str]

class ServerSearchResponse(BaseModel):
    results: List[ServerSearchResult]
    count: int

@router.get("/servers/search", response_model=ServerSearchResponse)
async def search_servers(
    name: Optional[str] = Query(None),
    registry_source: Optional[str] = Query(None),
    risk_tier: Optional[str] = Query(None),
    limit: int = Query(10),
    db_session=Depends(get_session)
):
    query = db_session.query(MCPServerRegistry)

    if name:
        query = query.filter(MCPServerRegistry.name.ilike(f"%{name}%"))

    if registry_source:
        query = query.filter(MCPServerRegistry.registry_source == registry_source)

    if risk_tier:
        query = query.filter(MCPServerRegistry.risk_tier == risk_tier)

    results = query.limit(limit).all()

    return {
        "results": [
            {
                "server_id": server.server_id,
                "name": server.name,
                "registry_source": server.registry_source,
                "url": server.url,
                "description": server.description,
                "risk_tier": server.risk_tier,
                "last_assessed": server.last_assessed
            } for server in results
        ],
        "count": len(results)
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_servers = [
        MCPServerRegistry(
            server_id="1",
            name="Test Server 1",
            registry_source="source1",
            url="http://test1.com",
            description="Test description 1",
            risk_tier="low",
            last_assessed="2023-01-01"
        ),
        MCPServerRegistry(
            server_id="2",
            name="Test Server 2",
            registry_source="source2",
            url="http://test2.com",
            description="Test description 2",
            risk_tier="medium",
            last_assessed="2023-01-02"
        ),
        MCPServerRegistry(
            server_id="3",
            name="Partial Match Server",
            registry_source="source1",
            url="http://partial.com",
            description="Partial match description",
            risk_tier="high",
            last_assessed="2023-01-03"
        )
    ]

    # Add test data to session
    session = SessionLocal()
    session.add_all(test_servers)
    session.commit()

    # Create test client
    client = TestClient(app)

    # Test name filter
    response = client.get("/servers/search?name=Test")
    assert len(response.json()["results"]) == 2
    assert all("Test Server" in server["name"] for server in response.json()["results"])

    # Test partial name match
    response = client.get("/servers/search?name=Partial")
    assert len(response.json()["results"]) == 1
    assert response.json()["results"][0]["name"] == "Partial Match Server"

    # Test registry_source filter
    response = client.get("/servers/search?registry_source=source1")
    assert len(response.json()["results"]) == 2
    assert all(server["registry_source"] == "source1" for server in response.json()["results"])

    # Test risk_tier filter
    response = client.get("/servers/search?risk_tier=medium")
    assert len(response.json()["results"]) == 1
    assert response.json()["results"][0]["risk_tier"] == "medium"

    # Test limit
    response = client.get("/servers/search?limit=1")
    assert len(response.json()["results"]) == 1

    # Test all filters combined
    response = client.get("/servers/search?name=Test&registry_source=source1&risk_tier=low")
    assert len(response.json()["results"]) == 1
    assert response.json()["results"][0]["name"] == "Test Server 1"

    print("PASS")