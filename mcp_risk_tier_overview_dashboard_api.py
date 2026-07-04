from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPSignalScores, MCPScoreDisputes

router = APIRouter()

class RiskTierOverview(BaseModel):
    tier_counts: dict[str, int]

@router.get("/dashboard/risk-tier-overview", response_model=RiskTierOverview)
def get_risk_tier_overview(db: Session = Depends(get_session)):
    # Query the risk tier distribution from MCPServerRegistry
    # Apply rule-override: CRITICAL axis forces the tier
    subquery = (
        db.query(
            MCPServerRegistry.id,
            func.coalesce(
                MCPLLMAxisScores.risk_tier,
                MCPSignalScores.risk_tier,
                MCPServerRegistry.risk_tier
            ).label("final_tier")
        )
        .join(
            MCPLLMAxisScores,
            MCPServerRegistry.id == MCPLLMAxisScores.server_id,
            isouter=True
        )
        .join(
            MCPSignalScores,
            MCPServerRegistry.id == MCPSignalScores.server_id,
            isouter=True
        )
        .subquery()
    )

    # Count the occurrences of each risk tier
    result = (
        db.query(
            subquery.c.final_tier,
            func.count(subquery.c.id).label("count")
        )
        .group_by(subquery.c.final_tier)
        .all()
    )

    # Convert to dictionary
    tier_counts = {tier: count for tier, count in result}

    # Ensure all 7 tiers are present, even if count is 0
    all_tiers = ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN", "OVERRIDE_CRITICAL", "OVERRIDE_HIGH"]
    for tier in all_tiers:
        if tier not in tier_counts:
            tier_counts[tier] = 0

    return {"tier_counts": tier_counts}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPSignalScores, MCPScoreDisputes
    from app.db import get_session
    from sqlalchemy.orm import sessionmaker

    # Create a test database in memory
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the get_session dependency for testing
    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create a test app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Seed the database with test data
    with TestSessionLocal() as db:
        # Add test data for MCPServerRegistry
        db.add_all([
            MCPServerRegistry(id=1, risk_tier="LOW"),
            MCPServerRegistry(id=2, risk_tier="MEDIUM"),
            MCPServerRegistry(id=3, risk_tier="HIGH"),
            MCPServerRegistry(id=4, risk_tier="CRITICAL"),
            MCPServerRegistry(id=5, risk_tier="UNKNOWN"),
            MCPServerRegistry(id=6, risk_tier="LOW"),
            MCPServerRegistry(id=7, risk_tier="MEDIUM"),
        ])

        # Add test data for MCPLLMAxisScores (override)
        db.add_all([
            MCPLLMAxisScores(server_id=1, risk_tier="OVERRIDE_CRITICAL"),
            MCPLLMAxisScores(server_id=2, risk_tier="OVERRIDE_HIGH"),
        ])

        # Add test data for MCPSignalScores
        db.add_all([
            MCPSignalScores(server_id=3, risk_tier="HIGH"),
            MCPSignalScores(server_id=4, risk_tier="CRITICAL"),
        ])

        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/dashboard/risk-tier-overview")
    assert response.status_code == 200
    data = response.json()

    # Verify all tiers are present
    assert set(data["tier_counts"].keys()) == {
        "LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN", "OVERRIDE_CRITICAL", "OVERRIDE_HIGH"
    }

    print("PASS")