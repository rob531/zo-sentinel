from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: datetime

class ServerHistoryResponse(BaseModel):
    server_id: str
    scores: List[AxisScore]

def get_server_history(
    server_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    session: Session = Depends(get_session)
) -> ServerHistoryResponse:
    # Verify server exists
    if not session.query(McpServerRegistry).filter_by(server_id=server_id).first():
        raise HTTPException(status_code=404, detail="Server not found")

    # Build query
    query = session.query(McpLlmAxisScore).filter_by(server_id=server_id)

    # Apply date filters if provided
    if start_date:
        query = query.filter(McpLlmAxisScore.scored_at >= start_date)
    if end_date:
        query = query.filter(McpLlmAxisScore.scored_at <= end_date)

    # Execute query and format results
    results = query.all()
    scores = [
        AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            scored_at=score.scored_at
        )
        for score in results
    ]

    return ServerHistoryResponse(server_id=server_id, scores=scores)

@router.get("/servers/{server_id}/history", response_model=ServerHistoryResponse)
async def server_history(
    server_id: str,
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    session: Session = Depends(get_session)
):
    return get_server_history(server_id, start_date, end_date, session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Create tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Seed test data
    from datetime import datetime, timedelta

    # Create test servers
    server1 = McpServerRegistry(server_id="test1", name="Test Server 1")
    server2 = McpServerRegistry(server_id="test2", name="Test Server 2")
    test_session.add_all([server1, server2])

    # Create test scores
    now = datetime.now()
    scores = [
        McpLlmAxisScore(
            server_id="test1",
            axis_name="axis1",
            label="Label 1",
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            scored_at=now - timedelta(days=3)
        ),
        McpLlmAxisScore(
            server_id="test1",
            axis_name="axis2",
            label="Label 2",
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6,
            scored_at=now - timedelta(days=2)
        ),
        McpLlmAxisScore(
            server_id="test1",
            axis_name="axis3",
            label="Label 3",
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            scored_at=now - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id="test2",
            axis_name="axis1",
            label="Label 1",
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            scored_at=now - timedelta(days=3)
        ),
        McpLlmAxisScore(
            server_id="test2",
            axis_name="axis2",
            label="Label 2",
            p_top=0.5,
            p_critical=0.4,
            p_danger=0.3,
            scored_at=now - timedelta(days=2)
        ),
        McpLlmAxisScore(
            server_id="test2",
            axis_name="axis3",
            label="Label 3",
            p_top=0.4,
            p_critical=0.3,
            p_danger=0.2,
            scored_at=now - timedelta(days=1)
        )
    ]
    test_session.add_all(scores)
    test_session.commit()

    # Setup FastAPI app with test session
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: test_session

    # Run tests
    client = TestClient(app)

    # Test server1 history
    response = client.get("/api/servers/test1/history")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test1"
    assert len(data["scores"]) == 3
    assert data["scores"][0]["axis_name"] == "axis1"
    assert data["scores"][0]["p_top"] == 0.9
    assert data["scores"][1]["axis_name"] == "axis2"
    assert data["scores"][1]["p_top"] == 0.8
    assert data["scores"][2]["axis_name"] == "axis3"
    assert data["scores"][2]["p_top"] == 0.7

    # Test server2 history with date filter
    start_date = (now - timedelta(days=3)).isoformat()
    end_date = (now - timedelta(days=2)).isoformat()
    response = client.get(f"/api/servers/test2/history?start_date={start_date}&end_date={end_date}")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test2"
    assert len(data["scores"]) == 2
    assert data["scores"][0]["axis_name"] == "axis1"
    assert data["scores"][0]["p_top"] == 0.6
    assert data["scores"][1]["axis_name"] == "axis2"
    assert data["scores"][1]["p_top"] == 0.5

    # Test non-existent server
    response = client.get("/api/servers/nonexistent/history")
    assert response.status_code == 404

    print("PASS")