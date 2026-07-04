from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScores
from sqlalchemy.orm import Session

router = APIRouter()

class AxisProbabilitiesResponse(BaseModel):
    axis_probabilities: Dict[str, Dict[str, float]]

@router.get("/server-axis-probabilities/{server_id}", response_model=AxisProbabilitiesResponse)
async def get_server_axis_probabilities(server_id: int, db: Session = Depends(get_session)):
    axis_scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="Server not found or no axis scores available")

    axis_probabilities = {}
    for score in axis_scores:
        if score.axis not in axis_probabilities:
            axis_probabilities[score.axis] = {}
        axis_probabilities[score.axis][score.label] = score.probability

    return {"axis_probabilities": axis_probabilities}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScores
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Seed test data
    test_server_id = 1
    test_data = [
        MCPLLMAxisScores(server_id=test_server_id, axis="axis1", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=test_server_id, axis="axis1", label="label2", probability=0.2),
        MCPLLMAxisScores(server_id=test_server_id, axis="axis2", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=test_server_id, axis="axis2", label="label2", probability=0.4),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Create a test client
    client = TestClient(router)

    # Test the endpoint
    response = client.get(f"/server-axis-probabilities/{test_server_id}")
    assert response.status_code == 200
    assert response.json() == {
        "axis_probabilities": {
            "axis1": {"label1": 0.8, "label2": 0.2},
            "axis2": {"label1": 0.6, "label2": 0.4}
        }
    }

    print("PASS")