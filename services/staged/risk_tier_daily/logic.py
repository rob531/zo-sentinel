from datetime import datetime, timedelta
from typing import List, Dict, Any
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel

class RiskTierSeriesItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierDailyResponse(BaseModel):
    days: int
    series: List[RiskTierSeriesItem]

def get_risk_tier_daily(days: int = 7, session: Session = Depends(get_session)) -> RiskTierDailyResponse:
    """
    Aggregates daily risk tier counts across all MCP servers for the past N days.

    Args:
        days: Number of days to look back (default: 7)
        session: SQLAlchemy session

    Returns:
        RiskTierDailyResponse containing the count of servers per risk tier per day
    """
    # Define risk tier thresholds
    tier_thresholds = [
        (75, 'TRUSTED_GENERAL'),
        (60, 'TRUSTED_RESEARCH'),
        (45, 'ENTERPRISE_CONTROLLED'),
        (30, 'CAUTION_LIMITED'),
        (15, 'HIGH_RISK_ISOLATED'),
        (0, 'KNOWN_THREAT')
    ]

    # Calculate date range
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    # Query to get server counts per risk tier per day
    query = session.query(
        McpLlmAxisScore.scored_at,
        McpServerRegistry.server_id,
        McpLlmAxisScore.overall_risk
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).all()

    # Process query results into series
    series = []
    for day in range(days):
        current_date = end_date - timedelta(days=day)
        date_str = current_date.isoformat()

        # Count servers per tier for this day
        tier_counts = {}
        for threshold, tier in tier_thresholds:
            tier_counts[tier] = 0

        for row in query:
            if row.scored_at.date() == current_date:
                score = row.overall_risk
                for threshold, tier in tier_thresholds:
                    if score >= threshold:
                        tier_counts[tier] += 1
                        break

        # Add to series
        for tier, count in tier_counts.items():
            series.append({
                'date': date_str,
                'tier': tier,
                'count': count
            })

    return RiskTierDailyResponse(days=days, series=series)

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.main import app
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(server_id='server1', name='Test Server 1'),
        McpServerRegistry(server_id='server2', name='Test Server 2'),
        McpLlmAxisScore(
            server_id='server1',
            axis_name='overall_risk',
            overall_risk=80,
            scored_at=datetime(2023, 1, 1)
        ),
        McpLlmAxisScore(
            server_id='server1',
            axis_name='overall_risk',
            overall_risk=50,
            scored_at=datetime(2023, 1, 2)
        ),
        McpLlmAxisScore(
            server_id='server2',
            axis_name='overall_risk',
            overall_risk=20,
            scored_at=datetime(2023, 1, 1)
        ),
        McpLlmAxisScore(
            server_id='server2',
            axis_name='overall_risk',
            overall_risk=10,
            scored_at=datetime(2023, 1, 2)
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get('/api/risk/tier/daily?days=2')

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data['days'] == 2
    assert len(data['series']) == 12  # 2 days * 6 tiers

    # Check specific count
    found = False
    for item in data['series']:
        if item['date'] == '2023-01-01' and item['tier'] == 'TRUSTED_GENERAL':
            assert item['count'] == 1
            found = True
            break
    assert found

    print("PASS")