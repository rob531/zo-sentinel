from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import McpServerRegistry
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api")

class TierDistribution(BaseModel):
    tier: str
    count: int
    percentage: float

class RiskTierDistributionResponse(BaseModel):
    tiers: List[TierDistribution]

@router.get("/risk/distribution", response_model=RiskTierDistributionResponse)
def get_risk_tier_distribution(session: Session = Depends(get_session)):
    total_servers = session.query(func.count(McpServerRegistry.server_id)).scalar()
    tier_distribution = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label('count')
    ).group_by(McpServerRegistry.risk_tier).all()

    tiers = []
    for tier, count in tier_distribution:
        percentage = (count / total_servers) * 100 if total_servers > 0 else 0
        tiers.append(TierDistribution(tier=tier, count=count, percentage=percentage))

    return RiskTierDistributionResponse(tiers=tiers)

if __name__ == "__main__":
    import sqlite3
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in-memory SQLite database
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables
    McpServerRegistry.__table__.create(bind=engine)

    # Create a sessionmaker
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency
    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    # Create a FastAPI app for testing
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed the database
    db = TestingSessionLocal()
    servers = [
        McpServerRegistry(server_id=f"server_{i}", risk_tier="low" if i % 2 == 0 else "high")
        for i in range(5)
    ]
    db.add_all(servers)
    db.commit()
    db.close()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/distribution")

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert len(data["tiers"]) == 2
    assert any(tier["count"] == 3 for tier in data["tiers"])

    print("PASS")