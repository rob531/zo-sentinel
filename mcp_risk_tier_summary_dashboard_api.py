from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from typing import Dict, List

from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class RiskTierSummary(BaseModel):
    tier_counts: Dict[str, int]
    top_servers_per_tier: Dict[str, List[str]]

@router.get("/dashboard/risk-tier-summary", response_model=RiskTierSummary)
def get_risk_tier_summary(db: Session = Depends(get_session)):
    # Get the count of servers per risk tier
    tier_counts = db.query(
        MCPServerRegistry.risk_tier,
        func.count(MCPServerRegistry.id).label("count")
    ).group_by(
        MCPServerRegistry.risk_tier
    ).all()

    tier_counts_dict = {tier: count for tier, count in tier_counts}

    # Get the top 5 servers per risk tier
    top_servers_query = db.query(
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.server_name
    ).order_by(
        MCPServerRegistry.risk_tier,
        desc(MCPServerRegistry.risk_score)
    ).all()

    top_servers_per_tier = {}
    for tier, server_name in top_servers_query:
        if tier not in top_servers_per_tier:
            top_servers_per_tier[tier] = []
        if len(top_servers_per_tier[tier]) < 5:
            top_servers_per_tier[tier].append(server_name)

    return {
        "tier_counts": tier_counts_dict,
        "top_servers_per_tier": top_servers_per_tier
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    from app.db import get_session
    from app import app
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        test_servers = [
            {"server_name": f"Server {i}", "risk_tier": tier, "risk_score": i}
            for i, tier in enumerate(["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Tier 6"] * 2)
        ]
        db.bulk_insert_mappings(MCPServerRegistry, test_servers)
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/dashboard/risk-tier-summary")
    assert response.status_code == 200
    data = response.json()

    # Verify all 6 tiers are present
    assert len(data["tier_counts"]) == 6
    assert all(tier in data["tier_counts"] for tier in ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Tier 6"])

    # Verify top 5 servers per tier
    for tier in data["top_servers_per_tier"]:
        assert len(data["top_servers_per_tier"][tier]) == 5

    print("PASS")