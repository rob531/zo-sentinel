from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel

class RiskTimelineEntry(BaseModel):
    date: str
    risk_tier: str
    overall_risk: float

class RiskTimelineResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[RiskTimelineEntry]

def calculate_overall_risk(axis_scores: List[McpLlmAxisScore]) -> float:
    if not axis_scores:
        return 0.0

    total_risk = sum(score.p_top for score in axis_scores)
    return total_risk / len(axis_scores)

def get_risk_tier(overall_risk: float) -> str:
    if overall_risk >= 0.9:
        return "high"
    elif overall_risk >= 0.7:
        return "medium"
    elif overall_risk >= 0.5:
        return "low"
    else:
        return "minimal"

def get_risk_timeline(server_id: str, days: int = 30) -> RiskTimelineResponse:
    session: Session = Depends(get_session)

    # Get the current risk tier and last assessed date
    current_registry = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not current_registry:
        raise HTTPException(status_code=404, detail="Server not found")

    # Calculate the start date for the timeline
    start_date = datetime.now() - timedelta(days=days)

    # Get all axis scores for the server within the date range
    axis_scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start_date
    ).order_by(McpLlmAxisScore.scored_at).all()

    if not axis_scores:
        return RiskTimelineResponse(
            server_id=server_id,
            days=days,
            timeline=[]
        )

    # Group axis scores by date
    scores_by_date = {}
    for score in axis_scores:
        date_str = score.scored_at.strftime("%Y-%m-%d")
        if date_str not in scores_by_date:
            scores_by_date[date_str] = []
        scores_by_date[date_str].append(score)

    # Calculate risk tier for each date
    timeline = []
    for date_str, scores in scores_by_date.items():
        overall_risk = calculate_overall_risk(scores)
        risk_tier = get_risk_tier(overall_risk)
        timeline.append({
            "date": date_str,
            "risk_tier": risk_tier,
            "overall_risk": overall_risk
        })

    # Sort timeline by date
    timeline.sort(key=lambda x: x["date"])

    return RiskTimelineResponse(
        server_id=server_id,
        days=days,
        timeline=timeline
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScore
    from app.router import app

    # Create in-memory SQLite database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)

    # Override the session dependency for testing
    from app.db import get_session
    app.dependency_overrides[get_session] = lambda: Session(test_engine)

    # Insert test data
    test_server_id = "test-server-123"

    # Insert registry entries
    session = Session(test_engine)
    session.add(McpServerRegistry(
        server_id=test_server_id,
        risk_tier="medium",
        last_assessed=datetime.now()
    ))
    session.commit()

    # Insert axis scores for two dates
    date1 = datetime.now() - timedelta(days=1)
    date2 = datetime.now() - timedelta(days=2)

    # Scores for date1 (should result in high risk)
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="axis1",
        p_top=0.95,
        scored_at=date1
    ))
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="axis2",
        p_top=0.95,
        scored_at=date1
    ))

    # Scores for date2 (should result in medium risk)
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="axis1",
        p_top=0.8,
        scored_at=date2
    ))
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="axis2",
        p_top=0.8,
        scored_at=date2
    ))

    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/api/server/{test_server_id}/risk_timeline?days=2")

    assert response.status_code == 200
    data = response.json()

    assert data["server_id"] == test_server_id
    assert data["days"] == 2
    assert len(data["timeline"]) == 2

    # Check the risk tiers for the known dates
    for entry in data["timeline"]:
        if entry["date"] == date1.strftime("%Y-%m-%d"):
            assert entry["risk_tier"] == "high"
        elif entry["date"] == date2.strftime("%Y-%m-%d"):
            assert entry["risk_tier"] == "medium"

    print("PASS")