from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScore
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float

class VerdictResponse(BaseModel):
    axes: Dict[str, AxisScore]
    overall: float
    risk_tier: str
    criteria_version: str

def get_server_verdict(server_id: str, db: Session = Depends(get_session)) -> Dict:
    # Query MCPLLMAxisScore for all rows matching server_id
    axis_scores = db.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="Server not found")

    # Group axis scores by axis_name
    axes = {}
    for score in axis_scores:
        if score.axis_name not in axes:
            axes[score.axis_name] = {
                "label": score.label,
                "p_top": score.p_top
            }

    # Calculate overall risk score (average of p_top values)
    overall = sum(score.p_top for score in axis_scores) / len(axis_scores)

    # Determine risk tier based on overall score
    if overall < 0.2:
        risk_tier = "Low"
    elif overall < 0.5:
        risk_tier = "Medium"
    elif overall < 0.8:
        risk_tier = "High"
    else:
        risk_tier = "Critical"

    # Get criteria version (assuming it's the same for all scores)
    criteria_version = axis_scores[0].criteria_version if axis_scores else "Unknown"

    return {
        "axes": axes,
        "overall": overall,
        "risk_tier": risk_tier,
        "criteria_version": criteria_version
    }

@router.get("/servers/{server_id}/verdict", response_model=VerdictResponse)
async def server_verdict_detail(server_id: str, db: Session = Depends(get_session)):
    return get_server_verdict(server_id, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import MCPLLMAxisScore, Base

    # Create a test app and override the database dependency
    app = FastAPI()
    app.include_router(router)

    # Create an in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_data = [
        MCPLLMAxisScore(
            server_id="test_server",
            axis_name="axis1",
            label="Label 1",
            p_top=0.1,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server",
            axis_name="axis2",
            label="Label 2",
            p_top=0.3,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server",
            axis_name="axis3",
            label="Label 3",
            p_top=0.5,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server",
            axis_name="axis4",
            label="Label 4",
            p_top=0.7,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server",
            axis_name="axis5",
            label="Label 5",
            p_top=0.2,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server",
            axis_name="axis6",
            label="Label 6",
            p_top=0.4,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server",
            axis_name="axis7",
            label="Label 7",
            p_top=0.6,
            criteria_version="v1.0"
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Create a test client and make a request
    client = TestClient(app)
    response = client.get("/servers/test_server/verdict")

    # Assert the response contains all 7 axes and correct tier
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 7
    assert data["risk_tier"] == "Medium"
    print("PASS")