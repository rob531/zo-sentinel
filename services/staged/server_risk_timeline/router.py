from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel

router = APIRouter(prefix="/api")

class RiskTimelineEntry(BaseModel):
    date: str
    risk_tier: str
    overall_risk: float

class RiskTimelineResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[RiskTimelineEntry]

def calculate_overall_risk(scores: List[McpLlmAxisScore]) -> float:
    if not scores:
        return 0.0
    return sum(score.p_top for score in scores) / len(scores)

def get_risk_timeline(server_id: str, days: int = 30, session: Session = Depends(get_session)) -> RiskTimelineResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get all scores for the server within the date range
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).order_by(McpLlmAxisScore.scored_at).all()

    # Group scores by date
    scores_by_date = {}
    for score in scores:
        date_str = score.scored_at.strftime("%Y-%m-%d")
        if date_str not in scores_by_date:
            scores_by_date[date_str] = []
        scores_by_date[date_str].append(score)

    # Get the server's current risk tier
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Build the timeline
    timeline = []
    for date_str in sorted(scores_by_date.keys()):
        scores = scores_by_date[date_str]
        overall_risk = calculate_overall_risk(scores)

        # Determine risk tier based on overall risk
        if overall_risk < 0.3:
            risk_tier = "LOW"
        elif overall_risk < 0.6:
            risk_tier = "MEDIUM"
        else:
            risk_tier = "HIGH"

        timeline.append({
            "date": date_str,
            "risk_tier": risk_tier,
            "overall_risk": overall_risk
        })

    return {
        "server_id": server_id,
        "days": days,
        "timeline": timeline
    }

router.get("/server/{server_id}/risk_timeline")(get_risk_timeline)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base

    # Create in-memory SQLite database for testing
    test_db = "sqlite:///:memory:"
    Base.metadata.create_all(bind=test_db)

    # Override the session dependency for testing
    def override_get_session():
        session = SessionLocal(bind=test_db)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create test client
    client = TestClient(router)

    # Insert test data
    from datetime import datetime
    from app.models import McpServerRegistry, McpLlmAxisScore

    test_server_id = "test-server-123"

    # Insert server registry
    test_server = McpServerRegistry(
        server_id=test_server_id,
        risk_tier="MEDIUM",
        last_assessed=datetime.utcnow()
    )
    session = SessionLocal(bind=test_db)
    session.add(test_server)
    session.commit()

    # Insert axis scores for two dates
    date1 = datetime.utcnow() - timedelta(days=2)
    date2 = datetime.utcnow() - timedelta(days=1)

    # Date 1 scores (low risk)
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="security",
        p_top=0.2,
        scored_at=date1
    ))
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="performance",
        p_top=0.2,
        scored_at=date1
    ))

    # Date 2 scores (high risk)
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="security",
        p_top=0.8,
        scored_at=date2
    ))
    session.add(McpLlmAxisScore(
        server_id=test_server_id,
        axis_name="performance",
        p_top=0.8,
        scored_at=date2
    ))

    session.commit()
    session.close()

    # Test the endpoint
    response = client.get(f"/server/{test_server_id}/risk_timeline?days=2")
    assert response.status_code == 200
    data = response.json()

    assert data["server_id"] == test_server_id
    assert data["days"] == 2
    assert len(data["timeline"]) == 2

    # Check date 1 (should be LOW risk)
    date1_entry = data["timeline"][0]
    assert date1_entry["date"] == date1.strftime("%Y-%m-%d")
    assert date1_entry["risk_tier"] == "LOW"

    # Check date 2 (should be HIGH risk)
    date2_entry = data["timeline"][1]
    assert date2_entry["date"] == date2.strftime("%Y-%m-%d")
    assert date2_entry["risk_tier"] == "HIGH"

    print("PASS")