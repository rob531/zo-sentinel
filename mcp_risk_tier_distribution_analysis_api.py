from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class RiskTierDistribution(BaseModel):
    tier_distribution: Dict[str, int]
    total_count: int

@router.get("/risk-tiers/distribution", response_model=RiskTierDistribution)
def get_risk_tier_distribution(db: Session = Depends(get_session)):
    query = db.query(
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.id
    ).all()

    tier_distribution = {}
    for tier, _ in query:
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

    total_count = len(query)

    return {
        "tier_distribution": tier_distribution,
        "total_count": total_count
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from app.dependency_overrides import override_get_session

    # Create a test database
    Base.metadata.create_all(engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = override_get_session

    # Create a test client
    client = TestClient(router)

    # Seed the database with test data
    test_data = [
        MCPServerRegistry(risk_tier="low"),
        MCPServerRegistry(risk_tier="medium"),
        MCPServerRegistry(risk_tier="high"),
        MCPServerRegistry(risk_tier="low"),
        MCPServerRegistry(risk_tier="medium"),
    ]

    with override_get_session() as session:
        session.add_all(test_data)
        session.commit()

    # Test the endpoint
    response = client.get("/risk-tiers/distribution")
    assert response.status_code == 200
    assert response.json() == {
        "tier_distribution": {"low": 2, "medium": 2, "high": 1},
        "total_count": 5
    }

    print("PASS")