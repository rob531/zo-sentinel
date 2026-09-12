from fastapi import Depends, FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, List

from app.db import get_session
from app.models import McpServerRegistry, Org

class ServerCountResponse(BaseModel):
    org_id: int
    name: str
    server_count: int
    risk_distribution: Dict[str, int]

def get_org_summary(org_id: int, session: Session = Depends(get_session)) -> ServerCountResponse:
    org = session.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    servers = session.query(McpServerRegistry).filter(McpServerRegistry.registry_source == org_id).all()

    risk_distribution = {}
    for server in servers:
        tier = server.risk_tier
        risk_distribution[tier] = risk_distribution.get(tier, 0) + 1

    return ServerCountResponse(
        org_id=org.id,
        name=org.name,
        server_count=len(servers),
        risk_distribution=risk_distribution
    )

def test_contract():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create in-memory SQLite database for testing
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Create test data
    test_org = Org(id=1, name="Test Org")
    test_servers = [
        McpServerRegistry(server_id=f"server_{i}", registry_source=1, risk_tier="low" if i < 2 else ("medium" if i < 4 else "high"))
        for i in range(5)
    ]

    # Insert test data
    session = SessionLocal()
    session.add(test_org)
    session.add_all(test_servers)
    session.commit()

    # Override dependency
    def override_get_session():
        return session

    # Create test app
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    # Test endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Add route for testing
    @app.get("/api/orgs/{org_id}/summary", response_model=ServerCountResponse)
    async def get_summary(org_id: int = Path(..., gt=0), session: Session = Depends(get_session)):
        return get_org_summary(org_id, session)

    # Make test request
    response = client.get("/api/orgs/1/summary")
    assert response.status_code == 200
    data = response.json()

    # Verify response
    assert data["org_id"] == 1
    assert data["name"] == "Test Org"
    assert data["server_count"] == 5
    assert data["risk_distribution"] == {"low": 2, "medium": 2, "high": 1}

    print("PASS")

if __name__ == "__main__":
    test_contract()