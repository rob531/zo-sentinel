from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Dict, List
from datetime import date

router = APIRouter()

class RiskTierTrend(BaseModel):
    date: date
    tier_counts: Dict[str, int]

def get_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry.models import RiskTier

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables and seed data
    from mcp_server_registry.models import Base
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()
    # Seed data
    from datetime import date, timedelta
    from mcp_server_registry.models import RiskTier
    for i in range(1, 6):
        d = date.today() - timedelta(days=i)
        for tier in ['low', 'medium', 'high']:
            session.add(RiskTier(date=d, tier=tier, count=i*10))
    session.commit()
    return session

@router.get("/risk-tiers/trend", response_model=List[RiskTierTrend])
async def get_risk_tier_trend(db: Session = Depends(get_db_session)):
    from mcp_server_registry.models import RiskTier

    # Query the database for risk tier trends
    query = (
        select(
            RiskTier.date,
            func.json_object_agg(RiskTier.tier, RiskTier.count).label("tier_counts")
        )
        .group_by(RiskTier.date)
        .order_by(RiskTier.date)
    )

    results = db.execute(query).fetchall()

    # Convert results to the expected format
    trend_data = []
    for row in results:
        trend_data.append({
            "date": row.date,
            "tier_counts": row.tier_counts
        })

    return trend_data

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/risk-tiers/trend")
    assert response.status_code == 200
    assert len(response.json()) > 0
    for item in response.json():
        assert "date" in item
        assert "tier_counts" in item
        assert isinstance(item["tier_counts"], dict)
        for tier, count in item["tier_counts"].items():
            assert isinstance(tier, str)
            assert isinstance(count, int)

    print("PASS")