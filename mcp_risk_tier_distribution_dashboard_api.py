from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict
from app.db import get_session
from app.models import McpRiskRegister

router = APIRouter()

class RiskTierDistribution(BaseModel):
    tier_counts: Dict[str, int]
    tier_percentages: Dict[str, float]

def calculate_risk_tier_distribution(session: Session) -> RiskTierDistribution:
    query = session.query(
        McpRiskRegister.risk_tier,
        func.count(McpRiskRegister.risk_tier).label('count')
    ).group_by(McpRiskRegister.risk_tier).all()

    total = sum(count for _, count in query)
    tier_counts = {tier: count for tier, count in query}
    tier_percentages = {tier: (count / total) * 100 for tier, count in query}

    return RiskTierDistribution(
        tier_counts=tier_counts,
        tier_percentages=tier_percentages
    )

@router.get("/risk-tier-distribution", response_model=RiskTierDistribution)
async def get_risk_tier_distribution(session: Session = Depends(get_session)):
    return calculate_risk_tier_distribution(session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpRiskRegister
    from sqlalchemy import create_engine, func

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)

    # Override dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: Session(test_engine)

    # Seed test data
    with Session(test_engine) as session:
        test_data = [
            McpRiskRegister(risk_tier="Tier 1"),
            McpRiskRegister(risk_tier="Tier 2"),
            McpRiskRegister(risk_tier="Tier 2"),
            McpRiskRegister(risk_tier="Tier 3"),
            McpRiskRegister(risk_tier="Tier 3"),
            McpRiskRegister(risk_tier="Tier 3"),
            McpRiskRegister(risk_tier="Tier 4"),
            McpRiskRegister(risk_tier="Tier 4"),
            McpRiskRegister(risk_tier="Tier 4"),
            McpRiskRegister(risk_tier="Tier 4"),
            McpRiskRegister(risk_tier="Tier 5"),
            McpRiskRegister(risk_tier="Tier 5"),
            McpRiskRegister(risk_tier="Tier 5"),
            McpRiskRegister(risk_tier="Tier 5"),
            McpRiskRegister(risk_tier="Tier 5"),
            McpRiskRegister(risk_tier="Tier 6"),
            McpRiskRegister(risk_tier="Tier 6"),
            McpRiskRegister(risk_tier="Tier 6"),
            McpRiskRegister(risk_tier="Tier 6"),
            McpRiskRegister(risk_tier="Tier 6"),
            McpRiskRegister(risk_tier="Tier 6"),
        ]
        session.add_all(test_data)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/risk-tier-distribution")
    assert response.status_code == 200
    data = response.json()

    # Verify all 6 tiers are present with counts and percentages
    assert set(data["tier_counts"].keys()) == {"Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Tier 6"}
    assert all(isinstance(count, int) for count in data["tier_counts"].values())
    assert all(isinstance(percent, float) for percent in data["tier_percentages"].values())
    print("PASS")