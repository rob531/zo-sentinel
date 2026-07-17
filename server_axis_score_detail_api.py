from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPLLMAxisScore
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class AxisScoreResponse(BaseModel):
    label: str
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    decision_rule_version: str
    model_version: str
    scored_at: str

VALID_AXES = [
    "security",
    "privacy",
    "reliability",
    "maintainability",
    "performance",
    "usability",
    "compliance"
]

@router.get(
    "/servers/{server_id}/axis/{axis_name}/score",
    response_model=AxisScoreResponse,
    status_code=status.HTTP_200_OK
)
async def get_axis_score(
    server_id: str,
    axis_name: str,
    db: Session = Depends(get_session)
):
    if axis_name not in VALID_AXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid axis name. Must be one of: {', '.join(VALID_AXES)}"
        )

    score = db.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == server_id,
        MCPLLMAxisScore.axis_name == axis_name
    ).first()

    if not score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Axis score not found"
        )

    return {
        "label": score.label,
        "probs": score.probs,
        "p_top": score.p_top,
        "p_critical": score.p_critical,
        "p_danger": score.p_danger,
        "decision_rule_version": score.decision_rule_version,
        "model_version": score.model_version,
        "scored_at": score.scored_at.isoformat()
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Insert test data
    test_data = {
        "server_id": "test-server-123",
        "axis_name": "security",
        "label": "High Risk",
        "probs": {"low": 0.1, "medium": 0.2, "high": 0.7},
        "p_top": 0.7,
        "p_critical": 0.5,
        "p_danger": 0.3,
        "decision_rule_version": "v1.0",
        "model_version": "llm-v2.1",
        "scored_at": datetime.now()
    }

    with TestSession() as session:
        session.add(MCPLLMAxisScore(**test_data))
        session.commit()

    client = TestClient(app)

    # Test the endpoint
    response = client.get(f"/servers/{test_data['server_id']}/axis/{test_data['axis_name']}/score")
    assert response.status_code == 200
    data = response.json()

    # Verify all expected keys are present
    expected_keys = [
        "label", "probs", "p_top", "p_critical", "p_danger",
        "decision_rule_version", "model_version", "scored_at"
    ]
    for key in expected_keys:
        assert key in data, f"Missing key: {key}"

    # Verify the data matches what we inserted
    assert data["label"] == test_data["label"]
    assert data["probs"] == test_data["probs"]
    assert data["p_top"] == test_data["p_top"]
    assert data["p_critical"] == test_data["p_critical"]
    assert data["p_danger"] == test_data["p_danger"]
    assert data["decision_rule_version"] == test_data["decision_rule_version"]
    assert data["model_version"] == test_data["model_version"]

    print("PASS")