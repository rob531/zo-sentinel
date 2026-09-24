"""
Verdict Watchlist Service - Returns servers from the watchlist.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict"])


class ServerResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str] = None
    last_scanned: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None

    class Config:
        from_attributes = True


class WatchlistResponse(BaseModel):
    servers: List[ServerResponse]


def get_watchlist_servers(
    session: Session,
    server_ids: Optional[List[str]] = None
) -> List[ServerResponse]:
    """
    Fetch servers from mcp_server_registry filtered by server_id IN (...) list.
    
    Args:
        session: SQLAlchemy database session
        server_ids: Optional list of server IDs to filter by. 
                    If None, returns all servers.
    
    Returns:
        List of ServerResponse objects with server details.
    """
    query = select(McpServerRegistry)
    
    if server_ids:
        query = query.where(McpServerRegistry.server_id.in_(server_ids))
    
    result = session.execute(query)
    rows = result.scalars().all()
    
    servers = []
    for row in rows:
        servers.append(ServerResponse(
            server_id=row.server_id,
            name=row.name,
            risk_tier=row.risk_tier,
            last_scanned=str(row.last_scanned) if row.last_scanned else None,
            url=row.url,
            description=row.description
        ))
    
    return servers


@router.get("/verdict/watchlist", response_model=WatchlistResponse)
def get_verdict_watchlist(
    server_ids: Optional[str] = None,
    session: Session = Depends(get_session)
) -> WatchlistResponse:
    """
    GET /api/verdict/watchlist
    
    Returns watchlist servers filtered by optional server_id list.
    
    Query params:
        server_ids: Comma-separated list of server IDs to filter by (optional)
    
    Returns:
        WatchlistResponse with list of servers
    """
    parsed_ids = None
    if server_ids:
        parsed_ids = [s.strip() for s in server_ids.split(",") if s.strip()]
    
    servers = get_watchlist_servers(session, parsed_ids)
    
    return WatchlistResponse(servers=servers)


# Public API functions used by other services
def list_servers(session: Session, server_ids: Optional[List[str]] = None) -> List[ServerResponse]:
    """Public API: List servers by IDs (used by router.py)."""
    return get_watchlist_servers(session, server_ids)


def get_server_by_id(session: Session, server_id: str) -> Optional[ServerResponse]:
    """Public API: Get a single server by ID."""
    servers = get_watchlist_servers(session, [server_id])
    return servers[0] if servers else None


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.pool import StaticPool
    
    # Set up path for imports
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    # Create in-memory SQLite database for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Create all tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    
    # Create session
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_session = TestingSessionLocal()
    
    try:
        # Seed 5 servers with mixed risk tiers
        from app.models import McpServerRegistry
        from datetime import datetime
        
        test_servers = [
            McpServerRegistry(
                server_id="srv_001",
                name="Production API Gateway",
                risk_tier="critical",
                last_scanned=datetime(2024, 1, 15, 10, 30, 0),
                url="https://api.production.example.com",
                description="Main production API gateway"
            ),
            McpServerRegistry(
                server_id="srv_002",
                name="Staging Auth Service",
                risk_tier="high",
                last_scanned=datetime(2024, 1, 14, 14, 20, 0),
                url="https://auth.staging.example.com",
                description="Staging authentication service"
            ),
            McpServerRegistry(
                server_id="srv_003",
                name="Dev Database Proxy",
                risk_tier="medium",
                last_scanned=datetime(2024, 1, 13, 9, 15, 0),
                url="https://dbproxy.dev.example.com",
                description="Development database proxy"
            ),
            McpServerRegistry(
                server_id="srv_004",
                name="Test Message Queue",
                risk_tier="low",
                last_scanned=datetime(2024, 1, 12, 16, 45, 0),
                url="https://mq.test.example.com",
                description="Testing message queue"
            ),
            McpServerRegistry(
                server_id="srv_005",
                name="CI Pipeline Runner",
                risk_tier="low",
                last_scanned=datetime(2024, 1, 11, 8, 0, 0),
                url="https://ci.example.com",
                description="CI/CD pipeline runner"
            ),
        ]
        
        for server in test_servers:
            test_session.add(server)
        test_session.commit()
        
        # Override the session dependency
        from app.db import get_session
        
        # Create test app with dependency override
        test_app = FastAPI()
        
        def override_get_session():
            try:
                yield test_session
            finally:
                pass
        
        test_app.dependency_overrides[get_session] = override_get_session
        
        # Include router
        test_app.include_router(router)
        
        # Test using TestClient
        from fastapi.testclient import TestClient
        client = TestClient(test_app)
        
        # Test 1: GET /api/verdict/watchlist with no filter (all servers)
        response = client.get("/api/verdict/watchlist")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "servers" in data, "Response missing 'servers' key"
        assert len(data["servers"]) == 5, f"Expected 5 servers, got {len(data['servers'])}"
        
        # Verify server structure
        for server in data["servers"]:
            assert "server_id" in server
            assert "name" in server
        
        # Test 2: GET with server_ids filter
        response = client.get("/api/verdict/watchlist?server_ids=srv_001,srv_003")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert len(data["servers"]) == 2, f"Expected 2 servers with filter, got {len(data['servers'])}"
        
        # Test 3: Verify risk tiers are present
        risk_tiers = [s["risk_tier"] for s in data["servers"]]
        assert "critical" in risk_tiers or "medium" in risk_tiers
        
        print("PASS")
        
    finally:
        test_session.close()