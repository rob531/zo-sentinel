from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from typing import Dict, Any

router = APIRouter()

class RiskTierSummary(BaseModel):
    total_count: int
    tier_distribution: Dict[str, int]

def get_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry import RiskTier

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables and seed data for testing
    from sqlalchemy import MetaData, Table, Column, Integer, String
    metadata = MetaData()
    risk_tiers = Table('risk_tiers', metadata,
        Column('id', Integer, primary_key=True),
        Column('tier', String),
    )
    metadata.create_all(engine)

    session = SessionLocal()
    session.execute(risk_tiers.insert(), [
        {'tier': 'low'},
        {'tier': 'medium'},
        {'tier': 'high'},
        {'tier': 'low'},
        {'tier': 'medium'},
    ])
    session.commit()

    return session

@router.get("/risk-tiers/summary", response_model=RiskTierSummary)
async def get_risk_tier_summary(db: Session = Depends(get_db_session)):
    from mcp_server_registry import RiskTier

    # Count total records
    total_count = db.query(func.count(RiskTier.id)).scalar()

    # Count records per tier
    tier_counts = db.query(
        RiskTier.tier,
        func.count(RiskTier.id).label('count')
    ).group_by(RiskTier.tier).all()

    tier_distribution = {tier: count for tier, count in tier_counts}

    return {
        "total_count": total_count,
        "tier_distribution": tier_distribution
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/risk-tiers/summary")
    assert response.status_code == 200
    assert response.json() == {
        "total_count": 5,
        "tier_distribution": {
            "low": 2,
            "medium": 2,
            "high": 1
        }
    }

    print("PASS")