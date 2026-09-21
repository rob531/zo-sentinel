from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from typing import List
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry

class FamilyDedupResponse(BaseModel):
    families: List[dict]

def get_duplicate_families(db: Session):
    # Query all servers from the registry
    servers = db.query(McpServerRegistry).all()

    # Group servers by family name (case-insensitive)
    family_groups = {}
    for server in servers:
        family_name = server.name.lower()
        if family_name not in family_groups:
            family_groups[family_name] = []
        family_groups[family_name].append(server)

    # Identify duplicate families (those with more than one server)
    duplicate_families = []
    for family_name, servers in family_groups.items():
        if len(servers) > 1:
            duplicate_families.append({
                "family_name": family_name,
                "server_count": len(servers)
            })

    return duplicate_families

def router():
    from fastapi import APIRouter
    router = APIRouter(prefix="/api/registry")

    @router.get("/family-dedup", response_model=FamilyDedupResponse)
    async def get_family_dedup(db: Session = Depends(get_session)):
        duplicate_families = get_duplicate_families(db)
        return {"families": duplicate_families}

    return router

def create_app():
    app = FastAPI()
    app.include_router(router())
    return app

if __name__ == "__main__":
    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    # Override the dependency to use the in-memory database
    from app.db import get_session as original_get_session
    def test_get_session():
        return Session(engine)

    app = create_app()
    app.dependency_overrides[original_get_session] = test_get_session

    # Seed test data
    with Session(engine) as session:
        test_servers = [
            McpServerRegistry(name="Family A", server_id="1", url="http://example.com/1"),
            McpServerRegistry(name="Family A", server_id="2", url="http://example.com/2"),
            McpServerRegistry(name="Family B", server_id="3", url="http://example.com/3"),
            McpServerRegistry(name="Family C", server_id="4", url="http://example.com/4"),
            McpServerRegistry(name="Family C", server_id="5", url="http://example.com/5"),
        ]
        session.add_all(test_servers)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/registry/family-dedup")

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert len(data["families"]) == 2
    assert data["families"][0]["family_name"] == "family a"
    assert data["families"][0]["server_count"] == 2
    assert data["families"][1]["family_name"] == "family c"
    assert data["families"][1]["server_count"] == 2

    print("PASS")