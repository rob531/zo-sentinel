from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter(prefix="/api")

class RiskTierSeriesItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierTrendResponse(BaseModel):
    server_id: str
    days: int
    series: List[RiskTierSeriesItem]

def get_risk_tier(score: float) -> str:
    if score >= 0.8:
        return "high"
    elif score >= 0.5:
        return "medium"
    else:
        return "low"

def get_risk_tier_trend(server_id: str, days: int, db: Session = Depends(get_session)) -> Dict:
    if days > 30:
        raise HTTPException(status_code=400, detail="days must be <= 30")

    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    query = db.query(
        func.date(McpLlmAxisScore.scored_at).label('date'),
        func.count(McpLlmAxisScore.id).label('count'),
        func.avg(McpLlmAxisScore.p_top).label('avg_score')
    ).join(
        McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.id
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).group_by(
        func.date(McpLlmAxisScore.scored_at)
    ).all()

    series = []
    for row in query:
        date_str = row.date.isoformat()
        tier = get_risk_tier(row.avg_score)
        series.append({
            "date": date_str,
            "tier": tier,
            "count": row.count
        })

    return {
        "server_id": server_id,
        "days": days,
        "series": series
    }

@router.get("/risk/tier_trend", response_model=RiskTierTrendResponse)
async def risk_tier_trend(server_id: str, days: int, db: Session = Depends(get_session)):
    return get_risk_tier_trend(server_id, days, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpLlmAxisScore, McpServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Create in-memory SQLite database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(id="server1", name="Test Server 1"),
        McpServerRegistry(id="server2", name="Test Server 2"),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            p_top=0.9,
            scored_at=datetime(2023, 1, 1)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            p_top=0.6,
            scored_at=datetime(2023, 1, 1)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            p_top=0.7,
            scored_at=datetime(2023, 1, 2)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            p_top=0.4,
            scored_at=datetime(2023, 1, 1)
        )
    ])
    test_session.commit()

    # Create test client
    client = TestClient(router)

    # Test the endpoint
    response = client.get("/risk/tier_trend?server_id=server1&days=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["series"]) == 2
    assert data["series"][0]["count"] == 2
    assert data["series"][0]["tier"] == "high"
    assert data["series"][1]["count"] == 1
    assert data["series"][1]["tier"] == "medium"

    print("PASS")