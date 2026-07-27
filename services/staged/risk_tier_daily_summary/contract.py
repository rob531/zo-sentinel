from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")

class RiskTierSeriesItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierDailySummaryResponse(BaseModel):
    days: int
    series: List[RiskTierSeriesItem]

def get_risk_tier_daily_summary(days: int, session: Session = Depends(get_session)) -> RiskTierDailySummaryResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    query = session.query(
        func.date_trunc('day', McpLlmAxisScore.scored_at).label('date'),
        McpLlmAxisScore.risk_tier,
        func.count(McpServerRegistry.server_id).label('count')
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        and_(
            McpLlmAxisScore.scored_at >= start_date,
            McpLlmAxisScore.scored_at <= end_date
        )
    ).group_by(
        func.date_trunc('day', McpLlmAxisScore.scored_at),
        McpLlmAxisScore.risk_tier
    ).order_by(
        func.date_trunc('day', McpLlmAxisScore.scored_at).desc(),
        McpLlmAxisScore.risk_tier
    ).all()

    series = [
        RiskTierSeriesItem(
            date=item.date.isoformat(),
            tier=item.risk_tier,
            count=item.count
        )
        for item in query
    ]

    return RiskTierDailySummaryResponse(days=days, series=series)

@router.get("/risk/daily_summary", response_model=RiskTierDailySummaryResponse)
async def risk_daily_summary(days: int, session: Session = Depends(get_session)):
    if days <= 0:
        raise HTTPException(status_code=400, detail="Days must be a positive integer")
    return get_risk_tier_daily_summary(days, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    from app.models import McpServerRegistry, McpLlmAxisScore
    from datetime import datetime, timedelta

    test_session = TestSession()

    # Create 3 servers with tier changes over 2 days
    server1 = McpServerRegistry(server_id="server1", name="Server 1")
    server2 = McpServerRegistry(server_id="server2", name="Server 2")
    server3 = McpServerRegistry(server_id="server3", name="Server 3")
    test_session.add_all([server1, server2, server3])

    # Day 1 scores
    day1 = datetime.utcnow() - timedelta(days=1)
    test_session.add_all([
        McpLlmAxisScore(server_id="server1", scored_at=day1, risk_tier="low"),
        McpLlmAxisScore(server_id="server2", scored_at=day1, risk_tier="medium"),
        McpLlmAxisScore(server_id="server3", scored_at=day1, risk_tier="low")
    ])

    # Day 2 scores
    day2 = datetime.utcnow()
    test_session.add_all([
        McpLlmAxisScore(server_id="server1", scored_at=day2, risk_tier="medium"),
        McpLlmAxisScore(server_id="server2", scored_at=day2, risk_tier="high"),
        McpLlmAxisScore(server_id="server3", scored_at=day2, risk_tier="low")
    ])

    test_session.commit()

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test endpoint
    response = client.get("/api/risk/daily_summary?days=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["series"]) == 4  # 2 days × 2 tiers
    assert any(item["count"] == 2 for item in data["series"])  # Known count value

    print("PASS")