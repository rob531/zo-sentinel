from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict
from datetime import date, datetime
from app.db import get_session
from app.models import McpRiskRegister
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

router = APIRouter()

class RiskTierCount(BaseModel):
    date: date
    counts: Dict[str, int]

class RiskTierTrendResponse(BaseModel):
    risk_tier_trend: List[RiskTierCount]

def get_risk_tier_counts(db: Session, start_date: date, end_date: date) -> List[RiskTierCount]:
    risk_tiers = [
        'TRUSTED_GENERAL',
        'TRUSTED_RESEARCH',
        'ENTERPRISE_CONTROLLED',
        'CAUTION_LIMITED',
        'HIGH_RISK_ISOLATED',
        'KNOWN_THREAT'
    ]

    date_range = []
    current_date = start_date
    while current_date <= end_date:
        date_range.append(current_date)
        current_date += datetime.timedelta(days=1)

    results = []
    for day in date_range:
        counts = {}
        for tier in risk_tiers:
            count = db.query(func.count(McpRiskRegister.id)).filter(
                McpRiskRegister.risk_tier == tier,
                McpRiskRegister.created_at >= day,
                McpRiskRegister.created_at < day + datetime.timedelta(days=1)
            ).scalar()
            counts[tier] = count if count is not None else 0
        results.append(RiskTierCount(date=day, counts=counts))

    return results

@router.get("/risk_tier_trend", response_model=RiskTierTrendResponse)
async def risk_tier_trend(
    start_date: date,
    end_date: date,
    db: Session = Depends(get_session)
):
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    return {"risk_tier_trend": get_risk_tier_counts(db, start_date, end_date)}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpRiskRegister
    from datetime import date, timedelta
    from sqlalchemy.orm import sessionmaker

    # Create a test database
    test_engine = engine
    Base.metadata.create_all(test_engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_data = [
        McpRiskRegister(
            id=1,
            risk_tier="TRUSTED_GENERAL",
            created_at=date(2023, 1, 1)
        ),
        McpRiskRegister(
            id=2,
            risk_tier="TRUSTED_RESEARCH",
            created_at=date(2023, 1, 1)
        ),
        McpRiskRegister(
            id=3,
            risk_tier="ENTERPRISE_CONTROLLED",
            created_at=date(2023, 1, 2)
        ),
        McpRiskRegister(
            id=4,
            risk_tier="CAUTION_LIMITED",
            created_at=date(2023, 1, 2)
        ),
        McpRiskRegister(
            id=5,
            risk_tier="HIGH_RISK_ISOLATED",
            created_at=date(2023, 1, 3)
        ),
        McpRiskRegister(
            id=6,
            risk_tier="KNOWN_THREAT",
            created_at=date(2023, 1, 3)
        ),
    ]

    db = SessionLocal()
    db.add_all(test_data)
    db.commit()

    # Create the FastAPI app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/risk_tier_trend?start_date=2023-01-01&end_date=2023-01-03")

    # Check the response
    assert response.status_code == 200
    data = response.json()
    assert len(data["risk_tier_trend"]) == 3

    for day_data in data["risk_tier_trend"]:
        assert "date" in day_data
        assert "counts" in day_data
        assert isinstance(day_data["counts"], dict)
        assert set(day_data["counts"].keys()) == {
            "TRUSTED_GENERAL",
            "TRUSTED_RESEARCH",
            "ENTERPRISE_CONTROLLED",
            "CAUTION_LIMITED",
            "HIGH_RISK_ISOLATED",
            "KNOWN_THREAT"
        }

    print("PASS")