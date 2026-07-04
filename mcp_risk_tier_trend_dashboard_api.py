from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime
from app.db import get_session
from app.models import MCPRiskRegister
from sqlalchemy import func
from sqlalchemy.orm import Session

router = APIRouter()

class RiskTierTrendPoint(BaseModel):
    timestamp: datetime
    risk_tier: int

class RiskTierTrendResponse(BaseModel):
    server_id: int
    trends: List[RiskTierTrendPoint]

@router.get("/dashboard/risk-tier-trend", response_model=RiskTierTrendResponse)
async def get_risk_tier_trend(server_id: int, session: Session = Depends(get_session)):
    trends = session.query(
        MCPRiskRegister.timestamp,
        MCPRiskRegister.risk_tier
    ).filter(
        MCPRiskRegister.server_id == server_id
    ).order_by(
        MCPRiskRegister.timestamp.asc()
    ).all()

    if not trends:
        raise HTTPException(status_code=404, detail="No risk tier data found for the given server_id")

    return RiskTierTrendResponse(
        server_id=server_id,
        trends=[RiskTierTrendPoint(timestamp=trend.timestamp, risk_tier=trend.risk_tier) for trend in trends]
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPRiskRegister
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timedelta

    # Override the session for testing
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Create test data
    test_server_id = 1
    test_data = [
        MCPRiskRegister(
            server_id=test_server_id,
            risk_tier=1,
            timestamp=datetime.now() - timedelta(days=5)
        ),
        MCPRiskRegister(
            server_id=test_server_id,
            risk_tier=2,
            timestamp=datetime.now() - timedelta(days=3)
        ),
        MCPRiskRegister(
            server_id=test_server_id,
            risk_tier=3,
            timestamp=datetime.now() - timedelta(days=1)
        )
    ]

    # Seed the database
    test_session.add_all(test_data)
    test_session.commit()

    # Override the dependency
    from app import app
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/dashboard/risk-tier-trend?server_id={test_server_id}")

    # Verify the response
    assert response.status_code == 200
    assert response.json()["server_id"] == test_server_id
    assert len(response.json()["trends"]) == 3
    assert response.json()["trends"][0]["risk_tier"] == 1
    assert response.json()["trends"][1]["risk_tier"] == 2
    assert response.json()["trends"][2]["risk_tier"] == 3

    print("PASS")