from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

class TierSummary(BaseModel):
    tier: str
    count: int
    percentage: float

class RiskTierSummary(BaseModel):
    total_servers: int
    tier_summaries: List[TierSummary]

@router.get("/risk/tier-summary", response_model=RiskTierSummary)
def get_risk_tier_summary(session: Session = Depends(get_session)):
    # Query distinct server_id and risk_tier where risk_tier is not null
    servers = session.query(McpServerRegistry.server_id, McpServerRegistry.risk_tier).filter(
        McpServerRegistry.risk_tier.isnot(None)
    ).distinct().all()

    # Count servers per tier
    tier_counts = {}
    for server in servers:
        tier = server.risk_tier
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Calculate total servers and percentages
    total_servers = sum(tier_counts.values())
    tier_summaries = []
    for tier, count in tier_counts.items():
        percentage = (count / total_servers) * 100
        tier_summaries.append(TierSummary(tier=tier, count=count, percentage=round(percentage, 2)))

    return RiskTierSummary(total_servers=total_servers, tier_summaries=tier_summaries)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Create test data
    from app.models import McpServerRegistry
    test_servers = [
        McpServerRegistry(server_id="server1", risk_tier="high"),
        McpServerRegistry(server_id="server2", risk_tier="medium"),
        McpServerRegistry(server_id="server3", risk_tier="high"),
        McpServerRegistry(server_id="server4", risk_tier="low"),
        McpServerRegistry(server_id="server5", risk_tier="medium"),
    ]
    session = SessionLocal()
    session.add_all(test_servers)
    session.commit()

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        return SessionLocal()

    app.dependency_overrides[get_session] = override_get_session

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/api/risk/tier-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 5
    assert len(data["tier_summaries"]) == 3
    assert sum(item["percentage"] for item in data["tier_summaries"]) == 100.0
    print("PASS")