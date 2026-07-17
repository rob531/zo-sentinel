from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Dict, Optional
import requests
from uuid import UUID
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class AxisScoreDetail(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    decision_rule_version: str

class ServerAxisScoreDetailResponse(BaseModel):
    server_id: str
    axes: Dict[str, AxisScoreDetail]

def get_axis_score_detail(server_id: str, include_meta: bool = False) -> dict:
    try:
        UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server_id format")

    session = next(get_session())
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axis_scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    result = {
        "server_id": server_id,
        "axes": {}
    }

    for score in axis_scores:
        result["axes"][score.axis] = {
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "decision_rule_version": score.decision_rule_version
        }

    if include_meta:
        result["meta"] = {
            "server_name": server.server_name,
            "org_id": server.org_id,
            "created_at": server.created_at.isoformat()
        }

    return result

@router.get("/servers/{server_id}/axis_scores/detail", response_model=ServerAxisScoreDetailResponse)
async def read_server_axis_score_detail(
    server_id: str,
    include_meta: bool = Query(False),
):
    return get_axis_score_detail(server_id, include_meta)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test data
    test_session = TestSessionLocal()
    test_server = MCPServerRegistry(
        server_id="550e8400-e29b-41d4-a716-446655440000",
        server_name="test-server",
        org_id="org-123",
        created_at="2023-01-01T00:00:00Z"
    )
    test_session.add(test_server)
    test_axis_score = MCPLLMAxisScores(
        server_id="550e8400-e29b-41d4-a716-446655440000",
        axis="test-axis",
        label="Test Axis",
        p_top=0.9,
        p_critical=0.8,
        p_danger=0.7,
        decision_rule_version="v1.0"
    )
    test_session.add(test_axis_score)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/550e8400-e29b-41d4-a716-446655440000/axis_scores/detail")
    assert response.status_code == 200
    assert response.json() == {
        "server_id": "550e8400-e29b-41d4-a716-446655440000",
        "axes": {
            "test-axis": {
                "label": "Test Axis",
                "p_top": 0.9,
                "p_critical": 0.8,
                "p_danger": 0.7,
                "decision_rule_version": "v1.0"
            }
        }
    }
    print("PASS")