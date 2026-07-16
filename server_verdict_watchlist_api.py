from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class ServerVerdict(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str

class PaginatedResponse(BaseModel):
    items: List[ServerVerdict]
    total: int
    page: int
    per_page: int

def get_watchlist_servers(db: Session, page: int = 1, per_page: int = 10) -> PaginatedResponse:
    offset = (page - 1) * per_page
    query = db.query(MCPServerRegistry).filter(MCPServerRegistry.tags.any(name='watchlist'))
    total = query.count()
    servers = query.offset(offset).limit(per_page).all()

    items = [
        ServerVerdict(
            server_id=server.server_id,
            name=server.name,
            verdict=server.verdict,
            risk_tier=server.risk_tier
        )
        for server in servers
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page
    )

@router.get("/watchlist/servers", response_model=PaginatedResponse)
async def watchlist_servers(
    db: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100)
):
    return get_watchlist_servers(db, page, per_page)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, Tag
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Seed test data
    test_session.add_all([
        MCPServerRegistry(
            server_id="server1",
            name="Test Server 1",
            verdict="clean",
            risk_tier="low",
            tags=[Tag(name="watchlist")]
        ),
        MCPServerRegistry(
            server_id="server2",
            name="Test Server 2",
            verdict="suspicious",
            risk_tier="medium",
            tags=[Tag(name="watchlist")]
        ),
        MCPServerRegistry(
            server_id="server3",
            name="Test Server 3",
            verdict="malicious",
            risk_tier="high",
            tags=[Tag(name="monitor")]
        )
    ])
    test_session.commit()

    # Override dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/watchlist/servers")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert all(server["tags"] == ["watchlist"] for server in data["items"])

    print("PASS")