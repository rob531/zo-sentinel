from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any
from datetime import date
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry
from fastapi.testclient import TestClient

router = APIRouter()

class RiskTierTrendResponse(BaseModel):
    data: Dict[date, Dict[str, int]]

@router.get("/mcp/risk-tier-trend", response_model=RiskTierTrendResponse)
def get_risk_tier_trend(db: Session = Depends(get_session)) -> Dict[date, Dict[str, int]]:
    query = db.query(
        MCPServerRegistry.date,
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.id
    ).all()

    trend_data = {}
    for record in query:
        record_date = record.date
        tier = record.risk_tier
        if record_date not in trend_data:
            trend_data[record_date] = {}
        if tier not in trend_data[record_date]:
            trend_data[record_date][tier] = 0
        trend_data[record_date][tier] += 1

    return {"data": trend_data}

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Seed test data
    test_data = [
        MCPServerRegistry(
            id=1,
            date=date(2023, 1, 1),
            risk_tier="high"
        ),
        MCPServerRegistry(
            id=2,
            date=date(2023, 1, 1),
            risk_tier="medium"
        ),
        MCPServerRegistry(
            id=3,
            date=date(2023, 1, 2),
            risk_tier="high"
        ),
        MCPServerRegistry(
            id=4,
            date=date(2023, 1, 2),
            risk_tier="low"
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Override the dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/mcp/risk-tier-trend")
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "2023-01-01": {"high": 1, "medium": 1},
            "2023-01-02": {"high": 1, "low": 1}
        }
    }

    print("PASS")