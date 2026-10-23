from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
from typing import Dict

router = APIRouter(prefix="/api/risk")

class RiskTierStatistics(BaseModel):
    total_servers: int
    risk_tier_counts: Dict[str, int]
    risk_tier_percentages: Dict[str, float]

@router.get("/statistics", response_model=RiskTierStatistics)
def get_risk_tier_statistics(session: Session = Depends(get_session)):
    # Query to get all servers with their risk tiers
    servers = session.query(McpServerRegistry).all()

    # Initialize counts for each risk tier
    risk_tier_counts = {
        "low": 0,
        "medium": 0,
        "high": 0,
        "critical": 0
    }

    # Count servers in each risk tier
    for server in servers:
        if server.risk_tier in risk_tier_counts:
            risk_tier_counts[server.risk_tier] += 1

    # Calculate total servers
    total_servers = sum(risk_tier_counts.values())

    # Calculate percentages for each risk tier
    risk_tier_percentages = {
        tier: (count / total_servers) * 100 if total_servers > 0 else 0.0
        for tier, count in risk_tier_counts.items()
    }

    return {
        "total_servers": total_servers,
        "risk_tier_counts": risk_tier_counts,
        "risk_tier_percentages": risk_tier_percentages
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Create a test FastAPI app
    test_app = FastAPI()
    test_app.include_router(router)

    # Override the get_session dependency for testing
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    from app.models import McpServerRegistry
    with SessionLocal() as session:
        session.add_all([
            McpServerRegistry(
                server_id="server1",
                risk_tier="low",
                name="Test Server 1",
                url="http://test1.example.com"
            ),
            McpServerRegistry(
                server_id="server2",
                risk_tier="medium",
                name="Test Server 2",
                url="http://test2.example.com"
            ),
            McpServerRegistry(
                server_id="server3",
                risk_tier="high",
                name="Test Server 3",
                url="http://test3.example.com"
            )
        ])
        session.commit()

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    response = client.get("/api/risk/statistics")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 3
    assert data["risk_tier_counts"]["low"] == 1
    print("PASS")