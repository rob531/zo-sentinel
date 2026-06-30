from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, List
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Assuming these are defined elsewhere in your project
from .models import RiskTierComparison, MCPServerRegistry
from .database import get_db

router = APIRouter()

class RiskTierComparisonResponse(BaseModel):
    group: str
    tier_counts: Dict[str, int]

@router.get("/risk-tier-comparison", response_model=List[RiskTierComparisonResponse])
async def get_risk_tier_comparison(db: Session = Depends(get_db)):
    # Calculate 24-hour cache expiration
    cache_expiry = datetime.utcnow() - timedelta(hours=24)

    # Query the database for risk tier comparisons
    query = (
        select(
            MCPServerRegistry.group,
            RiskTierComparison.tier,
            func.count(RiskTierComparison.id).label("count")
        )
        .join(MCPServerRegistry, MCPServerRegistry.id == RiskTierComparison.server_id)
        .where(RiskTierComparison.updated_at >= cache_expiry)
        .group_by(MCPServerRegistry.group, RiskTierComparison.tier)
    )

    result = db.execute(query).fetchall()

    # Format the result into the desired structure
    comparison_data = {}
    for row in result:
        group = row.group
        tier = row.tier
        count = row.count

        if group not in comparison_data:
            comparison_data[group] = {}

        comparison_data[group][tier] = count

    # Convert to list of RiskTierComparisonResponse
    response = [
        RiskTierComparisonResponse(
            group=group,
            tier_counts=tier_counts
        )
        for group, tier_counts in comparison_data.items()
    ]

    return response

if __name__ == "__main__":
    # Setup in-memory database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from .models import Base
    Base.metadata.create_all(bind=engine)

    # Seed test data
    def seed_test_data():
        db = SessionLocal()
        try:
            # Create test servers
            server1 = MCPServerRegistry(id=1, group="GroupA")
            server2 = MCPServerRegistry(id=2, group="GroupB")

            db.add(server1)
            db.add(server2)
            db.commit()

            # Create test risk tier comparisons
            from datetime import datetime
            now = datetime.utcnow()

            comparisons = [
                RiskTierComparison(server_id=1, tier="Tier1", updated_at=now),
                RiskTierComparison(server_id=1, tier="Tier2", updated_at=now),
                RiskTierComparison(server_id=1, tier="Tier3", updated_at=now),
                RiskTierComparison(server_id=1, tier="Tier4", updated_at=now),
                RiskTierComparison(server_id=1, tier="Tier5", updated_at=now),
                RiskTierComparison(server_id=1, tier="Tier6", updated_at=now),
                RiskTierComparison(server_id=2, tier="Tier1", updated_at=now),
                RiskTierComparison(server_id=2, tier="Tier2", updated_at=now),
                RiskTierComparison(server_id=2, tier="Tier3", updated_at=now),
                RiskTierComparison(server_id=2, tier="Tier4", updated_at=now),
                RiskTierComparison(server_id=2, tier="Tier5", updated_at=now),
                RiskTierComparison(server_id=2, tier="Tier6", updated_at=now),
            ]

            db.add_all(comparisons)
            db.commit()
        finally:
            db.close()

    seed_test_data()

    # Create FastAPI app for testing
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Override get_db for testing
    def get_test_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_test_db

    # Run test
    client = TestClient(app)
    response = client.get("/risk-tier-comparison")
    assert response.status_code == 200

    data = response.json()
    assert len(data) >= 2  # At least two groups
    for group_data in data:
        assert len(group_data["tier_counts"]) == 6  # All 6 tiers
        for tier, count in group_data["tier_counts"].items():
            assert count > 0  # Counts > 0

    print("PASS")