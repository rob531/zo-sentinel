from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter(prefix="/api/risk")

class TierDistribution(BaseModel):
    tier: str
    count: int

class RiskTierDistributionResponse(BaseModel):
    total: int
    tiers: List[TierDistribution]

def get_risk_tier_distribution(db: Session = Depends(get_session)) -> RiskTierDistributionResponse:
    """
    Returns the distribution of MCP servers across risk tiers.
    """
    try:
        # Query the database to get the count of servers in each risk tier
        results = db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.id).label('count')
        ).group_by(
            McpServerRegistry.risk_tier
        ).all()

        # Prepare the response data
        tiers = [
            {"tier": tier, "count": count}
            for tier, count in results
        ]

        total = sum(count for _, count in results)

        return RiskTierDistributionResponse(
            total=total,
            tiers=tiers
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

router.get("/tier_distribution")(get_risk_tier_distribution)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import McpServerRegistry, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Set up an in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency to use the test session
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create a test client
    client = TestClient(router)

    # Seed the database with test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(risk_tier="TRUSTED_GENERAL"),
        McpServerRegistry(risk_tier="ENTERPRISE_CONTROLLED"),
        McpServerRegistry(risk_tier="HIGH_RISK_ISOLATED"),
    ])
    test_session.commit()

    # Test the endpoint
    response = client.get("/tier_distribution")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["tiers"]) == 3
    assert any(tier["tier"] == "TRUSTED_GENERAL" and tier["count"] == 1 for tier in data["tiers"])
    assert any(tier["tier"] == "ENTERPRISE_CONTROLLED" and tier["count"] == 1 for tier in data["tiers"])
    assert any(tier["tier"] == "HIGH_RISK_ISOLATED" and tier["count"] == 1 for tier in data["tiers"])

    print("PASS")