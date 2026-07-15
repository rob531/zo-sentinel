from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScores
from sqlalchemy.orm import Session

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: Dict[str, float]

class AxisScoresResponse(BaseModel):
    server_id: str
    axes: Dict[str, AxisScore]

def get_axis_scores(server_id: str, db: Session = Depends(get_session)) -> Dict:
    scores = db.query(
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.label,
        MCPLLMAxisScores.p_top,
        MCPLLMAxisScores.p_critical,
        MCPLLMAxisScores.p_danger,
        MCPLLMAxisScores.probs
    ).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()

    if not scores:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = {
        score.axis_name: {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "probs": score.probs
        }
        for score in scores
    }

    return {"server_id": server_id, "axes": axes}

@router.get("/servers/{server_id}/axis_scores", response_model=AxisScoresResponse)
async def read_axis_scores(server_id: str, db: Session = Depends(get_session)):
    return get_axis_scores(server_id, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session as original_get_session

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[original_get_session] = override_get_session

    # Create test app and client
    test_app = FastAPI()
    test_app.include_router(router)
    client = TestClient(test_app)

    # Insert test data
    with TestingSessionLocal() as db:
        test_data = [
            MCPLLMAxisScores(
                server_id="abc123",
                axis_name="axis1",
                label="Label 1",
                p_top=0.9,
                p_critical=0.7,
                p_danger=0.5,
                probs={"a": 0.1, "b": 0.2, "c": 0.7}
            ),
            MCPLLMAxisScores(
                server_id="abc123",
                axis_name="axis2",
                label="Label 2",
                p_top=0.8,
                p_critical=0.6,
                p_danger=0.4,
                probs={"a": 0.2, "b": 0.3, "c": 0.5}
            ),
            MCPLLMAxisScores(
                server_id="abc123",
                axis_name="axis3",
                label="Label 3",
                p_top=0.7,
                p_critical=0.5,
                p_danger=0.3,
                probs={"a": 0.3, "b": 0.4, "c": 0.3}
            ),
            MCPLLMAxisScores(
                server_id="abc123",
                axis_name="axis4",
                label="Label 4",
                p_top=0.6,
                p_critical=0.4,
                p_danger=0.2,
                probs={"a": 0.4, "b": 0.5, "c": 0.1}
            ),
            MCPLLMAxisScores(
                server_id="abc123",
                axis_name="axis5",
                label="Label 5",
                p_top=0.5,
                p_critical=0.3,
                p_danger=0.1,
                probs={"a": 0.5, "b": 0.6, "c": 0.9}
            ),
            MCPLLMAxisScores(
                server_id="abc123",
                axis_name="axis6",
                label="Label 6",
                p_top=0.4,
                p_critical=0.2,
                p_danger=0.0,
                probs={"a": 0.6, "b": 0.7, "c": 0.8}
            ),
            MCPLLMAxisScores(
                server_id="abc123",
                axis_name="axis7",
                label="Label 7",
                p_top=0.3,
                p_critical=0.1,
                p_danger=0.0,
                probs={"a": 0.7, "b": 0.8, "c": 0.9}
            )
        ]
        db.add_all(test_data)
        db.commit()

    # Test the endpoint
    response = client.get("/servers/abc123/axis_scores")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "abc123"
    assert len(data["axes"]) == 7
    for axis in data["axes"].values():
        assert "label" in axis
        assert "p_top" in axis
        assert "p_critical" in axis
        assert "p_danger" in axis
        assert "probs" in axis

    print("PASS")