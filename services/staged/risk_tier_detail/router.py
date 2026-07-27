from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter()

class DayTier(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierDetailResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[DayTier]

def calculate_risk_tier(score: float) -> str:
    if score >= 0.9:
        return "Critical"
    elif score >= 0.7:
        return "High"
    elif score >= 0.5:
        return "Medium"
    elif score >= 0.3:
        return "Low"
    else:
        return "Minimal"

def get_risk_tier_detail(server_id: str, days: int, session: Session) -> RiskTierDetailResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    subquery = (
        session.query(
            McpLlmAxisScore.server_id,
            func.date(McpLlmAxisScore.created_at).label('date'),
            func.max(McpLlmAxisScore.overall_risk).label('max_score')
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.created_at >= start_date)
        .group_by(McpLlmAxisScore.server_id, func.date(McpLlmAxisScore.created_at))
        .subquery()
    )

    results = (
        session.query(
            subquery.c.date,
            subquery.c.max_score
        )
        .order_by(subquery.c.date)
        .all()
    )

    timeline = []
    for date, score in results:
        tier = calculate_risk_tier(score)
        timeline.append(DayTier(
            date=date.isoformat(),
            tier=tier,
            count=1
        ))

    return RiskTierDetailResponse(
        server_id=server_id,
        days=days,
        timeline=timeline
    )

@router.get("/api/server/{server_id}/risk_tier_detail", response_model=RiskTierDetailResponse)
async def risk_tier_detail(
    server_id: str,
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session)
):
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id cannot be empty")

    return get_risk_tier_detail(server_id, days, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session as original_get_session

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Override the dependency for testing
    app.dependency_overrides[original_get_session] = lambda: test_session

    # Create test data
    server1 = McpServerRegistry(server_id="server1", name="Test Server 1")
    server2 = McpServerRegistry(server_id="server2", name="Test Server 2")
    test_session.add_all([server1, server2])
    test_session.commit()

    # Add mock axis scores
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    scores = [
        McpLlmAxisScore(
            server_id="server1",
            created_at=now - timedelta(days=2),
            overall_risk=0.85
        ),
        McpLlmAxisScore(
            server_id="server1",
            created_at=now - timedelta(days=1),
            overall_risk=0.65
        ),
        McpLlmAxisScore(
            server_id="server1",
            created_at=now,
            overall_risk=0.92
        ),
        McpLlmAxisScore(
            server_id="server2",
            created_at=now - timedelta(days=2),
            overall_risk=0.45
        ),
    ]
    test_session.add_all(scores)
    test_session.commit()

    # Create a test client
    from main import app
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/server/server1/risk_tier_detail?days=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data["timeline"]) == 3
    assert data["timeline"][1]["tier"] == "High"  # Day 2 should be "High" based on score 0.65

    print("PASS")