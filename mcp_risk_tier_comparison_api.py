from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Dict

router = APIRouter()

class RiskTierComparison(BaseModel):
    average_score: float
    median_score: float

class RiskTierComparisonResponse(BaseModel):
    tier: Dict[str, RiskTierComparison]

def get_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry import RiskTier, Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed test data
    session = SessionLocal()
    session.add_all([
        RiskTier(tier="low", score=10),
        RiskTier(tier="low", score=20),
        RiskTier(tier="medium", score=30),
        RiskTier(tier="medium", score=40),
        RiskTier(tier="high", score=50),
        RiskTier(tier="high", score=60),
    ])
    session.commit()

    yield session
    session.close()

@router.get("/risk-tiers/comparison", response_model=RiskTierComparisonResponse)
def get_risk_tier_comparison(db: Session = Depends(get_db)):
    from mcp_server_registry import RiskTier

    # Calculate average and median scores for each tier
    stmt = (
        select(
            RiskTier.tier,
            func.avg(RiskTier.score).label("average_score"),
            func.percentile_cont(0.5).within_group(RiskTier.score).label("median_score")
        )
        .group_by(RiskTier.tier)
    )

    result = db.execute(stmt)
    comparison_data = {
        tier: {"average_score": average_score, "median_score": median_score}
        for tier, average_score, median_score in result
    }

    return {"tier": comparison_data}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/risk-tiers/comparison")
    assert response.status_code == 200
    assert response.json() == {
        "tier": {
            "low": {"average_score": 15.0, "median_score": 15.0},
            "medium": {"average_score": 35.0, "median_score": 35.0},
            "high": {"average_score": 55.0, "median_score": 55.0}
        }
    }

    print("PASS")