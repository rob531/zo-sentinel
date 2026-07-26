from fastapi import Depends
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from pydantic import BaseModel

class RiskTierSeriesItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierTrendResponse(BaseModel):
    server_id: str
    days: int
    series: List[RiskTierSeriesItem]

def get_risk_tier_trend(server_id: str, days: int, session: Session = Depends(get_session)) -> Dict:
    # Validate days parameter
    if days > 30:
        days = 30

    # Calculate date range
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    # Get server name
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        return {"error": "Server not found"}

    # Query scores in date range
    scores = session.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.p_top
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).all()

    # Group by date and calculate tier distribution
    date_tiers = {}
    for scored_at, p_top in scores:
        date = scored_at.date()
        if p_top >= 0.9:
            tier = "high"
        elif p_top >= 0.7:
            tier = "medium"
        else:
            tier = "low"

        if date not in date_tiers:
            date_tiers[date] = {}
        date_tiers[date][tier] = date_tiers[date].get(tier, 0) + 1

    # Prepare response
    series = []
    for date in sorted(date_tiers.keys()):
        tiers = date_tiers[date]
        for tier, count in tiers.items():
            series.append({
                "date": date.isoformat(),
                "tier": tier,
                "count": count
            })

    return {
        "server_id": server_id,
        "days": days,
        "series": series
    }

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        server1 = McpServerRegistry(server_id="server1", name="Test Server 1")
        server2 = McpServerRegistry(server_id="server2", name="Test Server 2")
        session.add_all([server1, server2])

        # Create test scores
        from datetime import datetime, timedelta
        today = datetime.utcnow().date()
        yesterday = today - timedelta(days=1)

        scores = [
            McpLlmAxisScore(
                server_id="server1",
                axis_name="overall_risk",
                p_top=0.95,
                scored_at=datetime.combine(today, datetime.min.time())
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis_name="overall_risk",
                p_top=0.85,
                scored_at=datetime.combine(today, datetime.min.time())
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis_name="overall_risk",
                p_top=0.75,
                scored_at=datetime.combine(yesterday, datetime.min.time())
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis_name="overall_risk",
                p_top=0.65,
                scored_at=datetime.combine(today, datetime.min.time())
            )
        ]
        session.add_all(scores)
        session.commit()

        # Test the function
        result = get_risk_tier_trend("server1", 2, session)

        # Verify results
        assert len(result["series"]) == 2
        assert any(item["date"] == today.isoformat() and item["tier"] == "high" and item["count"] == 1 for item in result["series"])
        assert any(item["date"] == today.isoformat() and item["tier"] == "medium" and item["count"] == 1 for item in result["series"])
        assert any(item["date"] == yesterday.isoformat() and item["tier"] == "medium" and item["count"] == 1 for item in result["series"])

        print("PASS")
    finally:
        session.close()