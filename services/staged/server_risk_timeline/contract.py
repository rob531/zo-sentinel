from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter(prefix="/api")

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
    return sum(score.p_top for score in axis_scores) / len(axis_scores)

def get_risk_timeline(server_id: str, days: int = 30) -> RiskTimelineResponse:
    session: Session = Depends(get_session)()

    try:
        # Get the current risk tier and last assessed date
        current_registry = session.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).first()

        if not current_registry:
            raise HTTPException(status_code=404, detail="Server not found")

        # Calculate the date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Get all axis scores within the date range
        axis_scores = session.query(McpLlmAxisScore).filter(
            and_(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.scored_at >= start_date,
                McpLlmAxisScore.scored_at <= end_date
            )
        ).order_by(McpLlmAxisScore.scored_at).all()

        # Group scores by date
        scores_by_date = {}
        for score in axis_scores:
            date_str = score.scored_at.strftime("%Y-%m-%d")
            if date_str not in scores_by_date:
                scores_by_date[date_str] = []
            scores_by_date[date_str].append(score)

        # Build the timeline
        timeline = []
        for date_str, scores in scores_by_date.items():
            overall_risk = calculate_overall_risk(scores)
            # Determine risk tier based on overall risk (simplified logic)
            if overall_risk >= 0.8:
                risk_tier = "High"
            elif overall_risk >= 0.5:
                risk_tier = "Medium"
            else:
                risk_tier = "Low"

            timeline.append({
                "date": date_str,
                "risk_tier": risk_tier,
                "overall_risk": overall_risk
            })

        # Sort by date
        timeline.sort(key=lambda x: x["date"])

        return RiskTimelineResponse(
            server_id=server_id,
            days=days,
            timeline=timeline
        )
    finally:
        session.close()

router.get("/server/{server_id}/risk_timeline", response_model=RiskTimelineResponse)(
    get_risk_timeline
)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()

    # Add test server registry entries
    test_session.add(McpServerRegistry(
        server_id="test_server_1",
        risk_tier="Medium",
        last_assessed=datetime.now()
    ))

    # Add test axis scores for two different dates
    test_date1 = datetime.now() - timedelta(days=2)
    test_date2 = datetime.now() - timedelta(days=1)

    test_session.add_all([
        McpLlmAxisScore(
            server_id="test_server_1",
            axis_name="axis1",
            p_top=0.6,
            scored_at=test_date1
        ),
        McpLlmAxisScore(
            server_id="test_server_1",
            axis_name="axis2",
            p_top=0.7,
            scored_at=test_date1
        ),
        McpLlmAxisScore(
            server_id="test_server_1",
            axis_name="axis1",
            p_top=0.8,
            scored_at=test_date2
        ),
        McpLlmAxisScore(
            server_id="test_server_1",
            axis_name="axis2",
            p_top=0.9,
            scored_at=test_date2
        )
    ])

    test_session.commit()

    # Create test client
    client = TestClient(router)

    # Test the endpoint
    response = client.get("/server/test_server_1/risk_timeline?days=3")
    assert response.status_code == 200

    data = response.json()
    assert data["server_id"] == "test_server_1"
    assert data["days"] == 3
    assert len(data["timeline"]) == 2

    # Verify the timeline entries
    for entry in data["timeline"]:
        if entry["date"] == test_date1.strftime("%Y-%m-%d"):
            assert entry["risk_tier"] == "Medium"
            assert entry["overall_risk"] == 0.65
        elif entry["date"] == test_date2.strftime("%Y-%m-%d"):
            assert entry["risk_tier"] == "High"
            assert entry["overall_risk"] == 0.85

    print("PASS")