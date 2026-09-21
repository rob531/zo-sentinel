from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")

class ServerInfo(BaseModel):
    server_id: str
    name: str
    url: str
    last_assessed: datetime

class ServersByRiskTierResponse(BaseModel):
    tier: str
    server_count: int
    servers: List[ServerInfo]

@router.get("/servers/by_risk_tier", response_model=ServersByRiskTierResponse)
async def get_servers_by_risk_tier(
    tier: str = Query(..., description="Risk tier to filter servers"),
    db: Session = Depends(get_session)
):
    servers = db.query(McpServerRegistry).filter(
        McpServerRegistry.risk_tier == tier
    ).all()

    if not servers:
        raise HTTPException(status_code=404, detail="No servers found for the given risk tier")

    server_info_list = [
        ServerInfo(
            server_id=server.server_id,
            name=server.name,
            url=server.url,
            last_assessed=server.last_assessed
        ) for server in servers
    ]

    return ServersByRiskTierResponse(
        tier=tier,
        server_count=len(servers),
        servers=server_info_list
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Create in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the dependency to use the test database
    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with TestSessionLocal() as session:
        session.add_all([
            McpServerRegistry(
                server_id="server1",
                name="High Risk Server",
                url="https://highrisk.example.com",
                last_assessed=datetime.now(),
                risk_tier="HIGH"
            ),
            McpServerRegistry(
                server_id="server2",
                name="Medium Risk Server",
                url="https://mediumrisk.example.com",
                last_assessed=datetime.now(),
                risk_tier="MEDIUM"
            ),
            McpServerRegistry(
                server_id="server3",
                name="Low Risk Server",
                url="https://lowrisk.example.com",
                last_assessed=datetime.now(),
                risk_tier="LOW"
            )
        ])
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/api/servers/by_risk_tier?tier=HIGH")

    assert response.status_code == 200
    assert response.json()["server_count"] == 1
    assert response.json()["servers"][0]["server_id"] == "server1"

    print("PASS")