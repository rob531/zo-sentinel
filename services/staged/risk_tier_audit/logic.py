from datetime import datetime, timedelta
from typing import List, Dict, Any
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
import requests
from pydantic import BaseModel

class TierTransition(BaseModel):
    date: str
    from_tier: str
    to_tier: str
    count: int

class RiskTierAuditResponse(BaseModel):
    days: int
    series: List[TierTransition]

def get_risk_tier_audit(days: int, session: Session = Depends(get_session)) -> RiskTierAuditResponse:
    # Get the current date and calculate the start date
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)

    # Query to get server risk tier changes
    query = """
    WITH daily_tiers AS (
        SELECT
            s.server_id,
            DATE(s.scored_at) AS day,
            r.risk_tier,
            LAG(r.risk_tier) OVER (PARTITION BY s.server_id ORDER BY DATE(s.scored_at)) AS prev_risk_tier
        FROM
            McpLlmAxisScore s
        JOIN
            McpServerRegistry r ON s.server_id = r.server_id
        WHERE
            DATE(s.scored_at) BETWEEN :start_date AND :end_date
    ),
    transitions AS (
        SELECT
            day,
            prev_risk_tier AS from_tier,
            risk_tier AS to_tier,
            COUNT(*) AS count
        FROM
            daily_tiers
        WHERE
            prev_risk_tier IS NOT NULL AND prev_risk_tier != risk_tier
        GROUP BY
            day, prev_risk_tier, risk_tier
    )
    SELECT
        day,
        from_tier,
        to_tier,
        count
    FROM
        transitions
    ORDER BY
        day
    """

    # Execute the query via write_service
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": query,
            "params": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            }
        }
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Database query failed")

    results = response.json()

    series = [
        TierTransition(
            date=row["day"],
            from_tier=row["from_tier"],
            to_tier=row["to_tier"],
            count=row["count"]
        )
        for row in results
    ]

    return RiskTierAuditResponse(days=days, series=series)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base

    # Setup in-memory SQLite for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test database
    Base.metadata.create_all(bind=SessionLocal().get_bind())

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        server1 = McpServerRegistry(server_id="server1", name="Test Server 1", risk_tier="low")
        server2 = McpServerRegistry(server_id="server2", name="Test Server 2", risk_tier="medium")
        server3 = McpServerRegistry(server_id="server3", name="Test Server 3", risk_tier="high")

        session.add_all([server1, server2, server3])
        session.commit()

        # Create test scores with tier transitions
        yesterday = datetime.now().date() - timedelta(days=1)
        today = datetime.now().date()

        # Day 1 scores
        session.add_all([
            McpLlmAxisScore(server_id="server1", axis_name="test_axis", p_top=0.9, scored_at=yesterday),
            McpLlmAxisScore(server_id="server2", axis_name="test_axis", p_top=0.8, scored_at=yesterday),
            McpLlmAxisScore(server_id="server3", axis_name="test_axis", p_top=0.7, scored_at=yesterday)
        ])

        # Update risk tiers for day 1
        server1.risk_tier = "medium"
        server2.risk_tier = "high"
        server3.risk_tier = "low"

        # Day 2 scores
        session.add_all([
            McpLlmAxisScore(server_id="server1", axis_name="test_axis", p_top=0.8, scored_at=today),
            McpLlmAxisScore(server_id="server2", axis_name="test_axis", p_top=0.7, scored_at=today),
            McpLlmAxisScore(server_id="server3", axis_name="test_axis", p_top=0.6, scored_at=today)
        ])

        # Update risk tiers for day 2
        server1.risk_tier = "high"
        server2.risk_tier = "low"
        server3.risk_tier = "medium"

        session.commit()

        # Test the endpoint
        client = TestClient(app)
        response = client.get("/api/risk/tier_audit?days=2")

        assert response.status_code == 200
        data = response.json()

        assert data["days"] == 2
        assert len(data["series"]) == 2

        # Verify known transition counts
        day1_transitions = [t for t in data["series"] if t["date"] == yesterday.isoformat()]
        day2_transitions = [t for t in data["series"] if t["date"] == today.isoformat()]

        assert len(day1_transitions) == 3
        assert len(day2_transitions) == 3

        print("PASS")
    finally:
        session.close()