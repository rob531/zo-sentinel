from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from app.db import get_session
from app.models import MCPRiskRegister

router = APIRouter()

class RiskTierSummary(BaseModel):
    tier: str
    count: int

@router.get("/risk-tier-summary-analysis", response_model=list[RiskTierSummary])
async def get_risk_tier_summary(db_session=Depends(get_session)):
    # Query the risk tier distribution from MCPRiskRegister
    query = db_session.query(
        MCPRiskRegister.risk_tier,
        func.count(MCPRiskRegister.risk_tier).label("count")
    ).group_by(MCPRiskRegister.risk_tier).all()

    # Convert query results to list of RiskTierSummary
    result = [RiskTierSummary(tier=tier, count=count) for tier, count in query]

    # Add the override tier if it exists
    override_tier = "CRITICAL"
    override_count = db_session.query(func.count(MCPRiskRegister.id)).filter(
        MCPRiskRegister.risk_tier == override_tier
    ).scalar()

    if override_count:
        result.append(RiskTierSummary(tier=override_tier, count=override_count))

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create a test client
    client = TestClient(router)

    # Seed the database with test data
    test_data = [
        {"risk_tier": "LOW", "description": "Test 1"},
        {"risk_tier": "MEDIUM", "description": "Test 2"},
        {"risk_tier": "HIGH", "description": "Test 3"},
        {"risk_tier": "CRITICAL", "description": "Test 4"},
        {"risk_tier": "LOW", "description": "Test 5"},
        {"risk_tier": "MEDIUM", "description": "Test 6"},
        {"risk_tier": "HIGH", "description": "Test 7"},
        {"risk_tier": "CRITICAL", "description": "Test 8"},
        {"risk_tier": "LOW", "description": "Test 9"},
        {"risk_tier": "MEDIUM", "description": "Test 10"},
        {"risk_tier": "HIGH", "description": "Test 11"},
        {"risk_tier": "CRITICAL", "description": "Test 12"},
        {"risk_tier": "LOW", "description": "Test 13"},
        {"risk_tier": "MEDIUM", "description": "Test 14"},
        {"risk_tier": "HIGH", "description": "Test 15"},
        {"risk_tier": "CRITICAL", "description": "Test 16"},
    ]

    with TestSessionLocal() as session:
        for data in test_data:
            session.add(MCPRiskRegister(**data))
        session.commit()

    # Test the endpoint
    response = client.get("/risk-tier-summary-analysis")
    assert response.status_code == 200
    data = response.json()

    # Check that all 7 tiers + the override tier are present
    tiers = [item["tier"] for item in data]
    assert len(tiers) == 4  # LOW, MEDIUM, HIGH, CRITICAL
    assert "LOW" in tiers
    assert "MEDIUM" in tiers
    assert "HIGH" in tiers
    assert "CRITICAL" in tiers

    print("PASS")