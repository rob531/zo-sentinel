from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.orm import Session
from typing import Dict, List
from app.db import get_session
from app.models import McpServerRegistry
from pydantic import BaseModel

router = APIRouter()

class RiskTierThreshold(BaseModel):
    min_score: float
    max_score: float

class RiskTierThresholds(BaseModel):
    tier_1: RiskTierThreshold
    tier_2: RiskTierThreshold
    tier_3: RiskTierThreshold
    tier_4: RiskTierThreshold
    tier_5: RiskTierThreshold
    tier_6: RiskTierThreshold

@router.get("/api/risk/thresholds", response_model=RiskTierThresholds)
async def get_risk_tier_thresholds(db: Session = Depends(get_session)) -> RiskTierThresholds:
    thresholds = {
        "tier_1": {"min_score": 0.0, "max_score": 0.2},
        "tier_2": {"min_score": 0.2, "max_score": 0.4},
        "tier_3": {"min_score": 0.4, "max_score": 0.6},
        "tier_4": {"min_score": 0.6, "max_score": 0.8},
        "tier_5": {"min_score": 0.8, "max_score": 0.9},
        "tier_6": {"min_score": 0.9, "max_score": 1.0}
    }
    return RiskTierThresholds(**thresholds)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/risk/thresholds")
    assert response.status_code == 200
    assert response.json() == {
        "tier_1": {"min_score": 0.0, "max_score": 0.2},
        "tier_2": {"min_score": 0.2, "max_score": 0.4},
        "tier_3": {"min_score": 0.4, "max_score": 0.6},
        "tier_4": {"min_score": 0.6, "max_score": 0.8},
        "tier_5": {"min_score": 0.8, "max_score": 0.9},
        "tier_6": {"min_score": 0.9, "max_score": 1.0}
    }

    print("PASS")