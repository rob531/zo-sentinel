from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float

class LatestAxisScoresResponse(BaseModel):
    axis_scores: Dict[str, AxisScore]

@router.get("/servers/{server_id}/latest-axis-scores", response_model=LatestAxisScoresResponse)
def get_latest_axis_scores(server_id: int, session: Session = Depends(get_session)):
    # Query the latest scores for each axis for the given server_id
    latest_scores = session.query(
        McpLlmAxisScores.axis_name,
        McpLlmAxisScores.label,
        McpLlmAxisScores.p_top
    ).filter(
        McpLlmAxisScores.server_id == server_id
    ).order_by(
        McpLlmAxisScores.axis_name,
        McpLlmAxisScores.timestamp.desc()
    ).distinct(
        McpLlmAxisScores.axis_name
    ).all()

    if not latest_scores:
        raise HTTPException(status_code=404, detail="No axis scores found for the given server_id")

    # Format the response
    axis_scores = {
        score.axis_name: {"label": score.label, "p_top": score.p_top}
        for score in latest_scores
    }

    return {"axis_scores": axis_scores}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpLlmAxisScores, McpServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Create a test database in memory
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed the test database
    test_session = TestSession()
    test_server = McpServerRegistry(server_id=1, name="Test Server")
    test_session.add(test_server)

    # Add test axis scores
    test_axes = [
        "security", "privacy", "reliability", "maintainability",
        "usability", "performance", "compliance"
    ]
    for axis in test_axes:
        test_score = McpLlmAxisScores(
            server_id=1,
            axis_name=axis,
            label=f"Test Label {axis}",
            p_top=0.9,
            timestamp="2023-01-01 00:00:00"
        )
        test_session.add(test_score)

    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/1/latest-axis-scores")

    # Verify the response
    assert response.status_code == 200
    response_data = response.json()
    assert "axis_scores" in response_data
    assert len(response_data["axis_scores"]) == 7
    for axis in test_axes:
        assert axis in response_data["axis_scores"]
        assert response_data["axis_scores"][axis]["label"] == f"Test Label {axis}"
        assert response_data["axis_scores"][axis]["p_top"] == 0.9

    print("PASS")