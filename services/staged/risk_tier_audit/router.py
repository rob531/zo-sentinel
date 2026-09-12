from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
import requests

router = APIRouter(prefix="/api/risk/tier_audit")

class TierTransition(BaseModel):
    date: str
    from_tier: str
    to_tier: str
    count: int

class TierAuditResponse(BaseModel):
    days: int
    series: List[TierTransition]

def get_tier_transitions(days: int, session: Session = Depends(get_session)) -> TierAuditResponse:
    try:
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Query to get servers with risk tier changes
        query = """
        WITH daily_tiers AS (
            SELECT
                s.server_id,
                DATE(s.scored_at) AS day,
                s.risk_tier,
                LAG(s.risk_tier) OVER (PARTITION BY s.server_id ORDER BY s.scored_at) AS prev_risk_tier
            FROM McpServerRegistry s
            JOIN McpLlmAxisScore a ON s.server_id = a.server_id
            WHERE s.scored_at BETWEEN :start_date AND :end_date
        )
        SELECT
            day,
            prev_risk_tier AS from_tier,
            risk_tier AS to_tier,
            COUNT(*) AS count
        FROM daily_tiers
        WHERE prev_risk_tier IS NOT NULL AND prev_risk_tier != risk_tier
        GROUP BY day, prev_risk_tier, risk_tier
        ORDER BY day
        """

        # Execute query via write_service
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
        response.raise_for_status()
        results = response.json()

        # Format results
        series = []
        for row in results:
            series.append({
                "date": row["day"],
                "from_tier": row["from_tier"],
                "to_tier": row["to_tier"],
                "count": row["count"]
            })

        return TierAuditResponse(days=days, series=series)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=TierAuditResponse)
async def tier_audit(days: int, session: Session = Depends(get_session)):
    return get_tier_transitions(days, session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="low",
            scored_at=datetime.now() - timedelta(days=2)
        ),
        McpServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="medium",
            scored_at=datetime.now() - timedelta(days=1)
        ),
        McpServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="low",
            scored_at=datetime.now() - timedelta(days=2)
        ),
        McpServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="high",
            scored_at=datetime.now() - timedelta(days=1)
        ),
        McpServerRegistry(
            server_id="server3",
            name="Test Server 3",
            risk_tier="low",
            scored_at=datetime.now() - timedelta(days=2)
        ),
        McpServerRegistry(
            server_id="server3",
            name="Test Server 3",
            risk_tier="low",
            scored_at=datetime.now() - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="test_axis",
            p_top=0.5,
            scored_at=datetime.now() - timedelta(days=2)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="test_axis",
            p_top=0.5,
            scored_at=datetime.now() - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="test_axis",
            p_top=0.5,
            scored_at=datetime.now() - timedelta(days=2)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="test_axis",
            p_top=0.5,
            scored_at=datetime.now() - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id="server3",
            axis_name="test_axis",
            p_top=0.5,
            scored_at=datetime.now() - timedelta(days=2)
        ),
        McpLlmAxisScore(
            server_id="server3",
            axis_name="test_axis",
            p_top=0.5,
            scored_at=datetime.now() - timedelta(days=1)
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier_audit?days=2")

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 2
    assert len(data["series"]) == 2

    # Check known transition counts
    transition_counts = {f"{t['from_tier']}-{t['to_tier']}": t['count'] for t in data["series"]}
    assert transition_counts["low-medium"] == 1
    assert transition_counts["low-high"] == 1

    print("PASS")