from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter(prefix="/api")

class DayTier(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierDetailResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[DayTier]

def get_risk_tier(score: float) -> str:
    if score >= 0.9:
        return "Critical"
    elif score >= 0.7:
        return "High"
    elif score >= 0.5:
        return "Medium"
    elif score >= 0.3:
        return "Low"
    else:
        return "Negligible"

def get_risk_tier_detail(server_id: str, days: int, db: Session) -> RiskTierDetailResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    subquery = (
        db.query(
            McpLlmAxisScore.server_id,
            func.date(McpLlmAxisScore.created_at).label('date'),
            func.max(McpLlmAxisScore.overall_risk).label('max_score')
        )
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.created_at >= start_date,
            McpLlmAxisScore.created_at <= end_date
        )
        .group_by(
            McpLlmAxisScore.server_id,
            func.date(McpLlmAxisScore.created_at)
        )
        .subquery()
    )

    results = (
        db.query(
            subquery.c.date,
            subquery.c.max_score,
            func.count().label('count')
        )
        .group_by(
            subquery.c.date,
            subquery.c.max_score
        )
        .all()
    )

    timeline = []
    for date, score, count in results:
        tier = get_risk_tier(score)
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

@router.get("/server/{server_id}/risk_tier_detail", response_model=RiskTierDetailResponse)
async def risk_tier_detail(
    server_id: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session)
):
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id cannot be empty")

    return get_risk_tier_detail(server_id, days, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Seed test data
    test_server1 = McpServerRegistry(server_id="test1", name="Test Server 1")
    test_server2 = McpServerRegistry(server_id="test2", name="Test Server 2")
    test_session.add_all([test_server1, test_server2])

    test_scores = [
        McpLlmAxisScore(
            server_id="test1",
            overall_risk=0.85,
            created_at=datetime.utcnow() - timedelta(days=2)
        ),
        McpLlmAxisScore(
            server_id="test1",
            overall_risk=0.75,
            created_at=datetime.utcnow() - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id="test1",
            overall_risk=0.65,
            created_at=datetime.utcnow()
        ),
        McpLlmAxisScore(
            server_id="test2",
            overall_risk=0.95,
            created_at=datetime.utcnow() - timedelta(days=2)
        ),
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Create test client
    client = TestClient(router)

    # Test endpoint
    response = client.get("/server/test1/risk_tier_detail?days=3")
    assert response.status_code == 200
    assert len(response.json()["timeline"]) == 3
    assert response.json()["timeline"][1]["tier"] == "High"

    print("PASS")