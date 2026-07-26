from typing import List, Dict, Any
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from pydantic import BaseModel

class TierDistribution(BaseModel):
    tier: str
    count: int

class RiskTierDistributionResponse(BaseModel):
    total: int
    tiers: List[TierDistribution]

def get_risk_tier_distribution(db: Session = Depends(get_session)) -> RiskTierDistributionResponse:
    """
    Get the distribution of MCP servers across risk tiers.

    Args:
        db: SQLAlchemy session

    Returns:
        RiskTierDistributionResponse: JSON object with total count and tier distribution
    """
    # Query the database for risk tier distribution
    results = db.query(
        McpServerRegistry.risk_tier,
        McpServerRegistry.risk_tier.label('tier'),
        McpServerRegistry.risk_tier.count().label('count')
    ).group_by(
        McpServerRegistry.risk_tier
    ).all()

    # Convert results to the expected format
    tiers = [{"tier": tier, "count": count} for tier, count in results]
    total = sum(count for _, count in results)

    return RiskTierDistributionResponse(total=total, tiers=tiers)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import Base, engine
    from app.models import McpServerRegistry

    # Create a test app and override the database dependency
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: Session(engine)

    # Create tables and seed test data
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            McpServerRegistry(risk_tier="TRUSTED_GENERAL"),
            McpServerRegistry(risk_tier="ENTERPRISE_CONTROLLED"),
            McpServerRegistry(risk_tier="HIGH_RISK_ISOLATED"),
        ])
        session.commit()

    # Add the endpoint to the test app
    @app.get("/api/risk/tier_distribution")
    async def tier_distribution():
        return get_risk_tier_distribution()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier_distribution")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["tiers"]) == 3
    tiers = {tier["tier"]: tier["count"] for tier in data["tiers"]}
    assert tiers["TRUSTED_GENERAL"] == 1
    assert tiers["ENTERPRISE_CONTROLLED"] == 1
    assert tiers["HIGH_RISK_ISOLATED"] == 1

    print("PASS")