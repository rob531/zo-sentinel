from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class RiskTierOverview(BaseModel):
    tier: str
    count: int
    percentage: float

@router.get("/mcp/risk-tier-overview", response_model=Dict[str, RiskTierOverview])
async def get_risk_tier_overview(db: Session = Depends(get_session)):
    query = """
    SELECT
        risk_tier,
        COUNT(*) as count,
        ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM mcp_server_registry), 2) as percentage
    FROM mcp_server_registry
    GROUP BY risk_tier
    ORDER BY risk_tier
    """
    result = db.execute(query)
    tiers = result.fetchall()

    overview = {
        tier: {
            "tier": tier,
            "count": count,
            "percentage": percentage
        }
        for tier, count, percentage in tiers
    }

    return overview

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from app.main import app

    # Create a test database in memory
    test_engine = engine
    Base.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    from app.db import get_session
    app.dependency_overrides[get_session] = lambda: Session(test_engine)

    # Seed test data
    with Session(test_engine) as session:
        test_tiers = ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Tier 6"]
        for tier in test_tiers:
            for _ in range(10):  # Add 10 entries per tier for test
                session.add(MCPServerRegistry(risk_tier=tier))
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/mcp/risk-tier-overview")
    assert response.status_code == 200
    data = response.json()

    # Verify all 6 tiers are present with percentages
    assert len(data) == 6
    for tier in test_tiers:
        assert tier in data
        assert "percentage" in data[tier]

    print("PASS")