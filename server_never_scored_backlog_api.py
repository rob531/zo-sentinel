from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class ServerNeverScoredResponse(BaseModel):
    id: int
    registry_source: str
    first_seen: str
    server_name: str
    server_address: str

class PaginatedResponse(BaseModel):
    items: List[ServerNeverScoredResponse]
    total: int
    page: int
    per_page: int

def get_never_scored_servers(
    session: Session,
    registry_source: Optional[str] = None,
    first_seen_after: Optional[str] = None,
    page: int = 1,
    per_page: int = 10
) -> PaginatedResponse:
    query = session.query(MCPServerRegistry).\
        join(MCPLLMAxisScores, MCPServerRegistry.id == MCPLLMAxisScores.server_id, isouter=True).\
        filter(MCPLLMAxisScores.id == None)

    if registry_source:
        query = query.filter(MCPServerRegistry.registry_source == registry_source)
    if first_seen_after:
        query = query.filter(MCPServerRegistry.first_seen >= first_seen_after)

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return PaginatedResponse(
        items=[ServerNeverScoredResponse(
            id=server.id,
            registry_source=server.registry_source,
            first_seen=str(server.first_seen),
            server_name=server.server_name,
            server_address=server.server_address
        ) for server in items],
        total=total,
        page=page,
        per_page=per_page
    )

@router.get("/servers/never_scored", response_model=PaginatedResponse)
async def never_scored_servers(
    registry_source: Optional[str] = Query(None),
    first_seen_after: Optional[str] = Query(None),
    page: int = Query(1),
    per_page: int = Query(10),
    session: Session = Depends(get_session)
):
    return get_never_scored_servers(
        session=session,
        registry_source=registry_source,
        first_seen_after=first_seen_after,
        page=page,
        per_page=per_page
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores
    from app.main import app
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(
            id=1,
            registry_source="source1",
            first_seen="2023-01-01",
            server_name="Server 1",
            server_address="192.168.1.1"
        ),
        MCPServerRegistry(
            id=2,
            registry_source="source2",
            first_seen="2023-01-02",
            server_name="Server 2",
            server_address="192.168.1.2"
        ),
        MCPServerRegistry(
            id=3,
            registry_source="source1",
            first_seen="2023-01-03",
            server_name="Server 3",
            server_address="192.168.1.3"
        ),
    ])
    test_session.commit()

    # Create test client
    client = TestClient(app)

    # Test endpoint
    response = client.get("/servers/never_scored")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 3

    response = client.get("/servers/never_scored?registry_source=source1")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2

    response = client.get("/servers/never_scored?first_seen_after=2023-01-02")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2

    response = client.get("/servers/never_scored?page=1&per_page=1")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1

    print("PASS")