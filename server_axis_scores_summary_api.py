from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import McpLlmAxisScores
from sqlalchemy.orm import Session

router = APIRouter()

class AxisScoreSummary(BaseModel):
    p_top: float
    p_critical: bool
    p_danger: bool

class ServerAxisScoresResponse(BaseModel):
    axis_scores: Dict[str, AxisScoreSummary]

@router.get("/servers/{server_id}/axis-scores-summary", response_model=ServerAxisScoresResponse)
async def get_server_axis_scores_summary(server_id: str, db: Session = Depends(get_session)):
    scores = db.query(
        McpLlmAxisScores.axis_name,
        McpLlmAxisScores.p_top,
        McpLlmAxisScores.p_critical,
        McpLlmAxisScores.p_danger
    ).filter(
        McpLlmAxisScores.server_id == server_id
    ).all()

    if not scores:
        raise HTTPException(status_code=404, detail="Server not found or no axis scores available")

    axis_scores = {
        score.axis_name: {
            "p_top": score.p_top,
            "p_critical": bool(score.p_critical),
            "p_danger": bool(score.p_danger)
        }
        for score in scores
    }

    return {"axis_scores": axis_scores}

def create_router(app: FastAPI) -> APIRouter:
    app.include_router(router)
    return router

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    create_router(app)

    client = TestClient(app)

    # Insert test data
    with TestSession() as session:
        test_data = [
            McpLlmAxisScores(
                server_id="test_server",
                axis_name="axis1",
                p_top=0.8,
                p_critical=True,
                p_danger=False
            ),
            McpLlmAxisScores(
                server_id="test_server",
                axis_name="axis2",
                p_top=0.6,
                p_critical=False,
                p_danger=True
            )
        ]
        session.add_all(test_data)
        session.commit()

    # Test endpoint
    response = client.get("/servers/test_server/axis-scores-summary")
    assert response.status_code == 200
    assert response.json() == {
        "axis_scores": {
            "axis1": {"p_top": 0.8, "p_critical": True, "p_danger": False},
            "axis2": {"p_top": 0.6, "p_critical": False, "p_danger": True}
        }
    }

    print("PASS")