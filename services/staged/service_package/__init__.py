# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
# deps: requests

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class ServerResponse(BaseModel):
    server_id: str
    name: str
    url: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None

    class Config:
        from_attributes = True


@router.get("/servers", response_model=List[ServerResponse])
def get_servers(
    db: Session = Depends(get_session),
) -> List[ServerResponse]:
    """Get all servers from the registry."""
    servers = db.query(McpServerRegistry).all()
    return [
        ServerResponse(
            server_id=s.server_id,
            name=s.name,
            url=s.url,
            trust_score=s.trust_score,
            verdict=s.verdict,
            confidence=s.confidence,
        )
        for s in servers
    ]


@router.get("/servers/{server_id}", response_model=ServerResponse)
def get_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerResponse:
    """Get a specific server by server_id."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found",
        )

    return ServerResponse(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        trust_score=server.trust_score,
        verdict=server.verdict,
        confidence=server.confidence,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )

    from app.models import Base

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    from app.models import McpServerRegistry as Server

    with TestingSessionLocal() as session:
        session.add(
            Server(
                server_id="test1",
                name="Test Server 1",
                url="http://test1.example.com",
                trust_score=0.85,
                verdict="LOW",
                confidence=0.9,
            )
        )
        session.commit()

    client = TestClient(test_app)

    try:
        response = client.get("/servers")
        assert response.status_code == 200, f"get_servers: {response.status_code}"
        data = response.json()
        assert len(data) == 1, f"expected 1 server, got {len(data)}"
        assert data[0]["server_id"] == "test1"

        response = client.get("/servers/test1")
        assert response.status_code == 200, f"get_server ok: {response.status_code}"
        assert response.json()["name"] == "Test Server 1"

        response = client.get("/servers/nonexistent")
        assert response.status_code == 404, f"get_server 404: {response.status_code}"

        print("PASS")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        exit(1)
