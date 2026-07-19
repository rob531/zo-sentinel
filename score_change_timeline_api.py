from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class AxisScores(BaseModel):
    axis: str
    p_top: float

class ScoreSnapshot(BaseModel):
    scored_at: str
    overall_risk_p_top: float
    tier: int
    axis_scores: List[AxisScores]

class ScoreTimelineResponse(BaseModel):
    server_id: str
    timeline: List[ScoreSnapshot]

def get_server_timeline(server_id: str, lookback_days: int, db: Session) -> List[ScoreSnapshot]:
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    scores = db.query(
        MCPLLMAxisScores.scored_at,
        MCPLLMAxisScores.overall_risk_p_top,
        MCPLLMAxisScores.tier,
        MCPLLMAxisScores.axis,
        MCPLLMAxisScores.p_top
    ).join(
        MCPServerRegistry, MCPServerRegistry.id == MCPLLMAxisScores.server_id
    ).filter(
        MCPServerRegistry.id == server_id,
        MCPLLMAxisScores.scored_at >= cutoff
    ).order_by(
        MCPLLMAxisScores.scored_at.asc()
    ).all()

    timeline = []
    current_snapshot = None

    for scored_at, overall_risk_p_top, tier, axis, p_top in scores:
        if not current_snapshot or current_snapshot.scored_at != scored_at.isoformat():
            current_snapshot = ScoreSnapshot(
                scored_at=scored_at.isoformat(),
                overall_risk_p_top=overall_risk_p_top,
                tier=tier,
                axis_scores=[]
            )
            timeline.append(current_snapshot)

        current_snapshot.axis_scores.append(AxisScores(axis=axis, p_top=p_top))

    return timeline

@router.get("/timeline/{server_id}", response_model=ScoreTimelineResponse)
async def get_score_timeline(
    server_id: str,
    lookback_days: Optional[int] = 30,
    db: Session = Depends(get_session)
):
    timeline = get_server_timeline(server_id, lookback_days, db)
    if not timeline:
        raise HTTPException(status_code=404, detail="Server not found or no scores in time range")

    return ScoreTimelineResponse(
        server_id=server_id,
        timeline=timeline
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_server = MCPServerRegistry(id="test-server")
    test_session.add(test_server)
    test_session.commit()

    test_scores = [
        MCPLLMAxisScores(
            server_id="test-server",
            scored_at=datetime.utcnow() - timedelta(days=2),
            overall_risk_p_top=0.8,
            tier=3,
            axis="security",
            p_top=0.7
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            scored_at=datetime.utcnow() - timedelta(days=2),
            overall_risk_p_top=0.8,
            tier=3,
            axis="compliance",
            p_top=0.6
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            scored_at=datetime.utcnow() - timedelta(days=1),
            overall_risk_p_top=0.7,
            tier=2,
            axis="security",
            p_top=0.6
        ),
        MCPLLMAxisScores(
            server_id="test-server",
            scored_at=datetime.utcnow() - timedelta(days=1),
            overall_risk_p_top=0.7,
            tier=2,
            axis="compliance",
            p_top=0.5
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/scoring/timeline/test-server")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test-server"
    assert len(data["timeline"]) == 2
    assert data["timeline"][0]["scored_at"].endswith("T00:00:00")
    assert isinstance(data["timeline"][0]["axis_scores"], list)
    assert len(data["timeline"][0]["axis_scores"]) == 2
    assert data["timeline"][0]["axis_scores"][0]["axis"] in ["security", "compliance"]

    print("PASS")