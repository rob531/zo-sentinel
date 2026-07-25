from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session

app = FastAPI()

class RiskHistoryEntry(BaseModel):
    date: str
    risk_tier: str
    overall_risk: float

class RiskHistoryResponse(BaseModel):
    server_id: str
    days: int
    history: List[RiskHistoryEntry]

def get_server_risk_history(server_id: str, days: int = 30, db: Session = Depends(get_session)) -> RiskHistoryResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get risk tier changes from server registry
    tier_changes = db.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_assessed
    ).filter(
        McpServerRegistry.server_id == server_id,
        McpServerRegistry.last_assessed >= start_date
    ).order_by(
        McpServerRegistry.last_assessed.asc()
    ).all()

    # Get daily risk scores from axis scores
    daily_scores = db.query(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.overall_risk
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start_date
    ).order_by(
        McpLlmAxisScore.scored_at.asc()
    ).all()

    # Combine and deduplicate by date
    history = {}
    for change in tier_changes:
        date = change.last_assessed.isoformat()
        history[date] = {
            "date": date,
            "risk_tier": change.risk_tier,
            "overall_risk": None
        }

    for score in daily_scores:
        date = score.scored_at.isoformat()
        if date in history:
            history[date]["overall_risk"] = score.overall_risk
        else:
            history[date] = {
                "date": date,
                "risk_tier": None,
                "overall_risk": score.overall_risk
            }

    # Convert to list and sort by date
    sorted_history = sorted(history.values(), key=lambda x: x["date"])

    return RiskHistoryResponse(
        server_id=server_id,
        days=days,
        history=sorted_history
    )

@app.get("/api/server/{server_id}/risk_history", response_model=RiskHistoryResponse)
async def server_risk_history(
    server_id: str,
    days: Optional[int] = Query(30, ge=1, le=365)
):
    return get_server_risk_history(server_id, days)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Insert test data
    with TestSession() as session:
        server_id = "test_server_123"
        session.add(McpServerRegistry(
            server_id=server_id,
            risk_tier="high",
            last_assessed=datetime.utcnow() - timedelta(days=2)
        ))
        session.add(McpServerRegistry(
            server_id=server_id,
            risk_tier="medium",
            last_assessed=datetime.utcnow() - timedelta(days=1)
        ))
        session.add(McpLlmAxisScore(
            server_id=server_id,
            scored_at=datetime.utcnow() - timedelta(days=2),
            overall_risk=0.85
        ))
        session.add(McpLlmAxisScore(
            server_id=server_id,
            scored_at=datetime.utcnow() - timedelta(days=1),
            overall_risk=0.65
        ))
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/api/server/{server_id}/risk_history?days=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["risk_tier"] == "high"
    assert data["history"][0]["overall_risk"] == 0.85

    print("PASS")