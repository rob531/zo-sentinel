from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class TierDistribution(BaseModel):
    count: int
    percentage: float

class RiskTierOverviewResponse(BaseModel):
    tiers: dict[str, TierDistribution]
    top_5_tiers: list[str]

@router.get("/mcp/risk-tier/overview/analysis", response_model=RiskTierOverviewResponse)
async def get_risk_tier_overview(db: Session = Depends(get_session)):
    # Get total count of servers
    total_servers = db.scalar(select(func.count()).select_from(MCPServerRegistry))

    # Get count of each risk tier
    tier_counts = db.execute(
        select(
            MCPServerRegistry.risk_tier,
            func.count(MCPServerRegistry.risk_tier).label("count")
        ).group_by(MCPServerRegistry.risk_tier)
    ).fetchall()

    # Calculate percentages and build response
    tiers = {}
    for tier, count in tier_counts:
        percentage = (count / total_servers) * 100
        tiers[tier] = {"count": count, "percentage": round(percentage, 2)}

    # Get top 5 tiers by count
    top_5_tiers = sorted(tier_counts, key=lambda x: x.count, reverse=True)[:5]
    top_5_tiers = [tier for tier, _ in top_5_tiers]

    return {
        "tiers": tiers,
        "top_5_tiers": top_5_tiers
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    with SessionLocal() as db:
        test_data = [
            {"risk_tier": "Tier 1"},
            {"risk_tier": "Tier 2"},
            {"risk_tier": "Tier 3"},
            {"risk_tier": "Tier 4"},
            {"risk_tier": "Tier 5"},
            {"risk_tier": "Tier 6"},
            {"risk_tier": "Tier 1"},
            {"risk_tier": "Tier 2"},
            {"risk_tier": "Tier 2"},
            {"risk_tier": "Tier 3"},
            {"risk_tier": "Tier 3"},
            {"risk_tier": "Tier 3"},
        ]
        for data in test_data:
            db.add(MCPServerRegistry(**data))
        db.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/mcp/risk-tier/overview/analysis")
    assert response.status_code == 200
    assert len(response.json()["tiers"]) == 6
    assert len(response.json()["top_5_tiers"]) == 5
    print("PASS")