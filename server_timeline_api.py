from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime
from app.db import get_session
from app.models import MCPLLMAxisScore
from sqlalchemy.orm import Session

router = APIRouter()

class AxisScore(BaseModel):
    scored_at: datetime
    axis_name: str
    p_top: float
    label: str

class ServerTimelineResponse(BaseModel):
    scores: List[AxisScore]

@router.get("/servers/{server_id}/timeline", response_model=ServerTimelineResponse)
def get_server_timeline(server_id: int, db: Session = Depends(get_session)):
    scores = db.query(
        MCPLLMAxisScore.scored_at,
        MCPLLMAxisScore.axis_name,
        MCPLLMAxisScore.p_top,
        MCPLLMAxisScore.label
    ).filter(
        MCPLLMAxisScore.server_id == server_id
    ).order_by(
        MCPLLMAxisScore.scored_at.asc()
    ).all()

    if not scores:
        raise HTTPException(status_code=404, detail="No scores found for this server")

    return {"scores": [{"scored_at": score.scored_at, "axis_name": score.axis_name, "p_top": score.p_top, "label": score.label} for score in scores]}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScore, MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(id=1, name="Test Server", description="Test Description")
    test_session.add(test_server)
    test_session.commit()

    test_scores = [
        MCPLLMAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 1, 12, 0),
            axis_name="axis1",
            p_top=0.9,
            label="high"
        ),
        MCPLLMAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 2, 12, 0),
            axis_name="axis2",
            p_top=0.8,
            label="medium"
        ),
        MCPLLMAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 3, 12, 0),
            axis_name="axis3",
            p_top=0.7,
            label="low"
        ),
        MCPLLMAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 4, 12, 0),
            axis_name="axis4",
            p_top=0.6,
            label="high"
        ),
        MCPLLMAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 5, 12, 0),
            axis_name="axis5",
            p_top=0.5,
            label="medium"
        ),
        MCPLLMAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 6, 12, 0),
            axis_name="axis6",
            p_top=0.4,
            label="low"
        ),
        MCPLLMAxisScore(
            server_id=1,
            scored_at=datetime(2023, 1, 7, 12, 0),
            axis_name="axis7",
            p_top=0.3,
            label="high"
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Create test client
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/servers/1/timeline")
    assert response.status_code == 200
    data = response.json()
    assert len(data["scores"]) >= 1
    assert all(axis["axis_name"] in ["axis1", "axis2", "axis3", "axis4", "axis5", "axis6", "axis7"] for axis in data["scores"])
    print("PASS")