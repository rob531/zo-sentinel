from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScore
from datetime import datetime

router = APIRouter()

class AxisDrift(BaseModel):
    axis_name: str
    current_p_top: float
    previous_p_top: float
    delta: float
    direction: str

class ScoringDriftResponse(BaseModel):
    server_id: int
    axes: List[AxisDrift]
    overall_drift: float
    scored_at_current: datetime
    scored_at_previous: datetime

def get_axis_drift(current: MCPLLMAxisScore, previous: MCPLLMAxisScore) -> AxisDrift:
    delta = current.p_top - previous.p_top
    direction = 'stable'
    if delta > 5:
        direction = 'up'
    elif delta < -5:
        direction = 'down'
    return AxisDrift(
        axis_name=current.axis_name,
        current_p_top=current.p_top,
        previous_p_top=previous.p_top,
        delta=delta,
        direction=direction
    )

@router.get("/servers/{server_id}/scoring-drift", response_model=ScoringDriftResponse)
async def get_scoring_drift(server_id: int, session: Session = Depends(get_session)):
    # Get the two most recent scores for each axis
    scores = session.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == server_id
    ).order_by(
        MCPLLMAxisScore.scored_at.desc()
    ).limit(2).all()

    if len(scores) < 2:
        raise HTTPException(status_code=404, detail="Not enough scores for drift calculation")

    # Group scores by axis_name
    axis_scores = {}
    for score in scores:
        if score.axis_name not in axis_scores:
            axis_scores[score.axis_name] = []
        axis_scores[score.axis_name].append(score)

    axes = []
    overall_drift = 0.0
    count = 0

    for axis_name, scores in axis_scores.items():
        if len(scores) >= 2:
            current = scores[0]
            previous = scores[1]
            axis_drift = get_axis_drift(current, previous)
            axes.append(axis_drift)
            overall_drift += axis_drift.delta
            count += 1

    if count == 0:
        raise HTTPException(status_code=404, detail="No valid axis pairs for drift calculation")

    overall_drift /= count

    return ScoringDriftResponse(
        server_id=server_id,
        axes=axes,
        overall_drift=overall_drift,
        scored_at_current=scores[0].scored_at,
        scored_at_previous=scores[1].scored_at
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScore
    from app.dependency_overrides import dependency_overrides
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Override get_session for testing
    dependency_overrides[get_session] = lambda: test_session

    # Seed test data
    test_server_id = 1
    test_data = [
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="axis1",
            p_top=50.0,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="axis1",
            p_top=55.0,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="axis2",
            p_top=30.0,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="axis2",
            p_top=25.0,
            scored_at=datetime.now()
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Create test client
    from app.main import app
    client = TestClient(app)

    # Test the endpoint
    response = client.get(f"/servers/{test_server_id}/scoring-drift")
    assert response.status_code == 200
    data = response.json()

    # Verify the response
    assert data["server_id"] == test_server_id
    assert len(data["axes"]) == 2

    axis1 = next(axis for axis in data["axes"] if axis["axis_name"] == "axis1")
    assert axis1["current_p_top"] == 55.0
    assert axis1["previous_p_top"] == 50.0
    assert axis1["delta"] == 5.0
    assert axis1["direction"] == "up"

    axis2 = next(axis for axis in data["axes"] if axis["axis_name"] == "axis2")
    assert axis2["current_p_top"] == 25.0
    assert axis2["previous_p_top"] == 30.0
    assert axis2["delta"] == -5.0
    assert axis2["direction"] == "down"

    assert data["overall_drift"] == 0.0

    print("PASS")

    # Cleanup
    test_session.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == test_server_id
    ).delete()
    test_session.commit()
    test_session.close()