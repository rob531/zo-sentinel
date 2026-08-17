# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

class ServerResponse:
    server_id: str
    name: str
    url: str
    trust_score: float
    verdict: str
    confidence: float

@router.get('/servers', response_model=List[ServerResponse])
async def get_servers(
    org_id: int,
    db: Session = Depends(get_session)
) -> List[ServerResponse]:
    """
    Get all servers for a given organization
    """
    servers = db.query(McpServerRegistry).filter(McpServerRegistry.org_id == org_id).all()
    return [
        ServerResponse(
            server_id=server.server_id,
            name=server.name,
            url=server.url,
            trust_score=server.trust_score,
            verdict=server.verdict,
            confidence=server.confidence
        )
        for server in servers
    ]

@router.get('/servers/{server_id}', response_model=ServerResponse)
async def get_server(
    server_id: str,
    org_id: int,
    db: Session = Depends(get_session)
) -> ServerResponse:
    """
    Get a specific server for a given organization
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id,
        McpServerRegistry.org_id == org_id
    ).first()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found"
        )
    
    return ServerResponse(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        trust_score=server.trust_score,
        verdict=server.verdict,
        confidence=server.confidence
    )

if __name__ == '__main__':
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # Setup test database
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    
    # Override dependency
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    # Test client
    client = TestClient(test_app)
    
    # Add test data
    from app.models import McpServerRegistry as Server
    with TestingSessionLocal() as session:
        session.add(Server(
            server_id="test1",
            name="Test Server 1",
            url="http://test1.example.com",
            trust_score=0.85,
            verdict="LOW",
            confidence=0.9,
            org_id=1
        ))
        session.commit()
    
    # Run tests
    try:
        # Test get_servers
        response = client.get('/servers?org_id=1')
        assert response.status_code == 200
        assert len(response.json()) == 1
        
        # Test get_server
        response = client.get('/servers/test1?org_id=1')
        assert response.status_code == 200
        assert response.json()['name'] == "Test Server 1"
        
        # Test get_server not found
        response = client.get('/servers/nonexistent?org_id=1')
        assert response.status_code == 404
        
        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {str(e)}")
