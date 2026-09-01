from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

class DayTier(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierDetailResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[DayTier]

def _map_to_risk_tier(score: float) -> str:
    if score >= 90:
        return "Critical"
    elif score >= 70:
        return "High"
    elif score >= 50:
        return "Medium"
    elif score >= 30:
        return "Low"
    else:
        return "Negligible"

def get_risk_tier_detail(server_id: str, days: int = 30, session: Session = Depends(get_session)) -> RiskTierDetailResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    subquery = (
        session.query(
            McpLlmAxisScore.server_id,
            func.date(McpLlmAxisScore.created_at).label('date'),
            McpLlmAxisScore.overall_risk,
            func.row_number().over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=McpLlmAxisScore.created_at.desc()
            ).label('rn')
        )
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.created_at >= start_date,
            McpLlmAxisScore.created_at <= end_date
        )
        .subquery()
    )

    results = (
        session.query(
            subquery.c.date,
            subquery.c.overall_risk,
            func.count().label('count')
        )
        .filter(subquery.c.rn == 1)
        .group_by(subquery.c.date, subquery.c.overall_risk)
        .order_by(subquery.c.date)
        .all()
    )

    timeline = []
    for date, score, count in results:
        tier = _map_to_risk_tier(score)
        timeline.append(DayTier(
            date=date.isoformat(),
            tier=tier,
            count=count
        ))

    return RiskTierDetailResponse(
        server_id=server_id,
        days=days,
        timeline=timeline
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    server1 = McpServerRegistry(server_id="server1", name="Test Server 1")
    server2 = McpServerRegistry(server_id="server2", name="Test Server 2")
    session.add_all([server1, server2])
    session.commit()

    # Add mock axis scores for server1
    now = datetime.utcnow()
    scores = [
        McpLlmAxisScore(
            server_id="server1",
            created_at=now - timedelta(days=2),
            overall_risk=85.0
        ),
        McpLlmAxisScore(
            server_id="server1",
            created_at=now - timedelta(days=1),
            overall_risk=75.0
        ),
        McpLlmAxisScore(
            server_id="server1",
            created_at=now,
            overall_risk=65.0
        )
    ]
    session.add_all(scores)
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/server/server1/risk_tier_detail?days=3")

    assert response.status_code == 200
    data = response.json()
    assert len(data["timeline"]) == 3
    assert data["timeline"][1]["tier"] == "High"  # Day 2 score was 75.0

    print("PASS")