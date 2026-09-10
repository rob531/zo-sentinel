from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
import requests
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter(prefix="/api")

class RiskTierTransition(BaseModel):
    date: str
    from_tier: str
    to_tier: str
    count: int

class RiskTierAuditResponse(BaseModel):
    days: int
    series: List[RiskTierTransition]

@router.get("/risk/tier_audit", response_model=RiskTierAuditResponse)
async def get_risk_tier_audit(days: int, db: Session = Depends(get_session)):
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be positive")

    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    query = """
    WITH daily_tiers AS (
        SELECT
            s.server_id,
            DATE(s.scored_at) AS day,
            r.risk_tier,
            ROW_NUMBER() OVER (PARTITION BY s.server_id, DATE(s.scored_at) ORDER BY s.scored_at DESC) AS rn
        FROM McpLlmAxisScore s
        JOIN McpServerRegistry r ON s.server_id = r.server_id
        WHERE DATE(s.scored_at) BETWEEN :start_date AND :end_date
    ),
    transitions AS (
        SELECT
            dt1.day,
            dt1.risk_tier AS from_tier,
            dt2.risk_tier AS to_tier,
            COUNT(DISTINCT dt1.server_id) AS count
        FROM daily_tiers dt1
        JOIN daily_tiers dt2 ON dt1.server_id = dt2.server_id AND dt1.day = dt2.day - INTERVAL '1 day'
        WHERE dt1.rn = 1 AND dt2.rn = 1 AND dt1.risk_tier != dt2.risk_tier
        GROUP BY dt1.day, dt1.risk_tier, dt2.risk_tier
    )
    SELECT
        day AS date,
        from_tier,
        to_tier,
        count
    FROM transitions
    ORDER BY day
    """

    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }

    response = requests.post("http://127.0.0.1:8772/query", json={"query": query, "params": params})
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Database query failed")

    results = response.json()
    series = [
        RiskTierTransition(
            date=row["date"],
            from_tier=row["from_tier"],
            to_tier=row["to_tier"],
            count=row["count"]
        )
        for row in results
    ]

    return RiskTierAuditResponse(days=days, series=series)

if __name__ == "__main__":
    from app.db import Base, engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Create test servers
        server1 = McpServerRegistry(server_id=1, name="Server 1", risk_tier="low")
        server2 = McpServerRegistry(server_id=2, name="Server 2", risk_tier="medium")
        server3 = McpServerRegistry(server_id=3, name="Server 3", risk_tier="high")
        session.add_all([server1, server2, server3])

        # Create test scores with tier transitions
        yesterday = datetime.utcnow() - timedelta(days=1)
        today = datetime.utcnow()

        # Day 1
        session.add(McpLlmAxisScore(server_id=1, axis_name="axis1", p_top=0.8, scored_at=yesterday))
        session.add(McpLlmAxisScore(server_id=2, axis_name="axis1", p_top=0.6, scored_at=yesterday))
        session.add(McpLlmAxisScore(server_id=3, axis_name="axis1", p_top=0.4, scored_at=yesterday))

        # Day 2 (transitions)
        session.add(McpLlmAxisScore(server_id=1, axis_name="axis1", p_top=0.7, scored_at=today))
        session.add(McpLlmAxisScore(server_id=2, axis_name="axis1", p_top=0.5, scored_at=today))
        session.add(McpLlmAxisScore(server_id=3, axis_name="axis1", p_top=0.3, scored_at=today))

        # Update risk tiers
        server1.risk_tier = "medium"
        server2.risk_tier = "high"
        server3.risk_tier = "low"

        session.commit()

    # Create test client
    client = TestClient(router)

    # Test the endpoint
    response = client.get("/risk/tier_audit?days=2")
    assert response.status_code == 200

    data = response.json()
    assert data["days"] == 2
    assert len(data["series"]) == 2

    # Verify transition counts
    transitions = {f"{t['from_tier']}->{t['to_tier']}": t['count'] for t in data["series"]}
    assert transitions.get("low->medium") == 1
    assert transitions.get("medium->high") == 1
    assert transitions.get("high->low") == 1

    print("PASS")