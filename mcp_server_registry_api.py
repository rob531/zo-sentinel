from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session

router = APIRouter()

class ServerRegistryEntry(BaseModel):
    server_id: str
    trust_score: float
    last_heartbeat: str

    class Config:
        orm_mode = True

@router.get("/mcp/server_registry", response_model=List[ServerRegistryEntry])
async def list_server_registry(
    server_id: Optional[str] = Query(None, description="Filter by server ID"),
    min_trust_score: Optional[float] = Query(None, description="Filter by minimum trust score"),
    session: Session = Depends(get_session)
):
    query = session.query(MCPServerRegistry)

    if server_id:
        query = query.filter(MCPServerRegistry.server_id == server_id)

    if min_trust_score is not None:
        query = query.filter(MCPServerRegistry.trust_score >= min_trust_score)

    servers = query.all()
    return servers

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import pytest

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    def test_list_server_registry():
        db = TestingSessionLocal()
        db.add(MCPServerRegistry(server_id="server1", trust_score=0.9, last_heartbeat="2023-01-01T00:00:00"))
        db.add(MCPServerRegistry(server_id="server2", trust_score=0.8, last_heartbeat="2023-01-02T00:00:00"))
        db.commit()

        response = client.get("/mcp/server_registry")
        assert response.status_code == 200
        assert len(response.json()) == 2

        response = client.get("/mcp/server_registry?server_id=server1")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["server_id"] == "server1"

        response = client.get("/mcp/server_registry?min_trust_score=0.85")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["server_id"] == "server1"

    test_list_server_registry()
    print("PASS")