from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/risk")

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

    query = (
        session.query(
            func.date(McpLlmAxisScore.scored_at).label('date'),
            McpLlmAxisScore.risk_tier.label('tier'),
            func.count(distinct=McpServerRegistry.server_id).label('count')
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .filter(
            and_(
                McpLlmAxisScore.scored_at >= start_date,
                McpLlmAxisScore.scored_at <= end_date
            )
        )
        .group_by('date', 'tier')
        .order_by('date', 'tier')
    )

    results = query.all()

    series = [
        RiskTierSeriesItem(
            date=row.date.isoformat(),
            tier=row.tier,
            count=row.count
        )
        for row in results
    ]

    return RiskTierDailySummaryResponse(days=days, series=series)

@router.get("/daily_summary", response_model=RiskTierDailySummaryResponse)
async def daily_summary(days: int = Query(7), session: Session = Depends(get_session)):
    return get_risk_tier_daily_summary(days, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    with SessionLocal() as session:
        # Create test servers
        server1 = McpServerRegistry(server_id="server1", hostname="server1.example.com")
        server2 = McpServerRegistry(server_id="server2", hostname="server2.example.com")
        server3 = McpServerRegistry(server_id="server3", hostname="server3.example.com")
        session.add_all([server1, server2, server3])

        # Create test scores for day 1
        day1 = datetime.utcnow() - timedelta(days=1)
        score1_day1 = McpLlmAxisScore(
            server_id="server1",
            risk_tier="low",
            scored_at=day1,
            llm_axis="test_axis",
            score=0.1
        )
        score2_day1 = McpLlmAxisScore(
            server_id="server2",
            risk_tier="high",
            scored_at=day1,
            llm_axis="test_axis",
            score=0.9
        )
        score3_day1 = McpLlmAxisScore(
            server_id="server3",
            risk_tier="low",
            scored_at=day1,
            llm_axis="test_axis",
            score=0.2
        )

        # Create test scores for day 2 (today)
        day2 = datetime.utcnow()
        score1_day2 = McpLlmAxisScore(
            server_id="server1",
            risk_tier="high",
            scored_at=day2,
            llm_axis="test_axis",
            score=0.8
        )
        score2_day2 = McpLlmAxisScore(
            server_id="server2",
            risk_tier="low",
            scored_at=day2,
            llm_axis="test_axis",
            score=0.3
        )
        score3_day2 = McpLlmAxisScore(
            server_id="server3",
            risk_tier="high",
            scored_at=day2,
            llm_axis="test_axis",
            score=0.7
        )

        session.add_all([
            score1_day1, score2_day1, score3_day1,
            score1_day2, score2_day2, score3_day2
        ])
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/daily_summary?days=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data["series"]) == 4  # 2 days × 2 tiers
    assert any(item["count"] == 2 for item in data["series"])  # At least one tier has 2 servers

    print("PASS")