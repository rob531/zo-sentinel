from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/risk")

class TierDistribution(BaseModel):
    tier: str
    count: int
    percentage: float

class RiskTierOverview(BaseModel):
    total_servers: int
    tiers: List[TierDistribution]

@router.get("/overview", response_model=RiskTierOverview)
def get_risk_tier_overview(session: Session = Depends(get_session)):
    # Get all servers with their risk tiers
    servers = session.query(McpServerRegistry).all()

    # Initialize tier counts
    tier_counts = {
        "TRUSTED_GENERAL": 0,
        "TRUSTED_RESEARCH": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "CAUTION_LIMITED": 0,
        "HIGH_RISK_ISOLATED": 0,
        "KNOWN_THREAT": 0,
        "INSUFFICIENT": 0
    }

    # Count servers per tier
    for server in servers:
        if server.risk_tier in tier_counts:
            tier_counts[server.risk_tier] += 1

    total_servers = sum(tier_counts.values())

    # Calculate percentages
    tiers = []
    for tier, count in tier_counts.items():
        percentage = (count / total_servers) * 100 if total_servers > 0 else 0.0
        tiers.append({
            "tier": tier,
            "count": count,
            "percentage": round(percentage, 2)
        })

    return {
        "total_servers": total_servers,
        "tiers": tiers
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        test_servers = [
            McpServerRegistry(server_id=f"server_{i}", name=f"Test Server {i}", risk_tier=tier)
            for i, tier in enumerate([
                "TRUSTED_GENERAL", "TRUSTED_GENERAL",
                "TRUSTED_RESEARCH", "TRUSTED_RESEARCH",
                "ENTERPRISE_CONTROLLED", "ENTERPRISE_CONTROLLED",
                "CAUTION_LIMITED", "CAUTION_LIMITED",
                "HIGH_RISK_ISOLATED", "HIGH_RISK_ISOLATED",
                "KNOWN_THREAT", "KNOWN_THREAT",
                "INSUFFICIENT", "INSUFFICIENT"
            ])
        ]
        session.add_all(test_servers)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/overview")
    assert response.status_code == 200
    data = response.json()

    assert data["total_servers"] == 14
    assert len(data["tiers"]) == 7
    for tier in data["tiers"]:
        assert tier["count"] == 2

    print("PASS")