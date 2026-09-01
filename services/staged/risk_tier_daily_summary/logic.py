from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

class RiskTierDailySummaryResponse(BaseModel):
    days: int
    series: List[dict]

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
        func.date_trunc('day', McpLlmAxisScore.scored_at)
    )

    results = query.all()

    series = [
        {
            "date": result.date.isoformat(),
            "tier": result.risk_tier,
            "count": result.count
        }
        for result in results
    ]

    return RiskTierDailySummaryResponse(days=days, series=series)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Insert test data
    with TestSessionLocal() as session:
        # Create test servers
        server1 = McpServerRegistry(server_id="server1", name="Server 1")
        server2 = McpServerRegistry(server_id="server2", name="Server 2")
        server3 = McpServerRegistry(server_id="server3", name="Server 3")
        session.add_all([server1, server2, server3])
        session.commit()

        # Create test scores
        yesterday = datetime.utcnow() - timedelta(days=1)
        today = datetime.utcnow()

        scores = [
            McpLlmAxisScore(
                server_id="server1",
                scored_at=yesterday,
                risk_tier="low"
            ),
            McpLlmAxisScore(
                server_id="server2",
                scored_at=yesterday,
                risk_tier="high"
            ),
            McpLlmAxisScore(
                server_id="server3",
                scored_at=yesterday,
                risk_tier="low"
            ),
            McpLlmAxisScore(
                server_id="server1",
                scored_at=today,
                risk_tier="high"
            ),
            McpLlmAxisScore(
                server_id="server2",
                scored_at=today,
                risk_tier="high"
            ),
            McpLlmAxisScore(
                server_id="server3",
                scored_at=today,
                risk_tier="low"
            )
        ]
        session.add_all(scores)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/daily_summary?days=2")

    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 2
    assert len(data["series"]) == 4  # 2 days × 2 tiers
    assert any(item["count"] == 2 for item in data["series"])

    print("PASS")