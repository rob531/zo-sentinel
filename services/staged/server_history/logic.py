from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/servers")

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
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Build query with date filters
    query = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    )

    if start_date:
        query = query.filter(McpLlmAxisScore.scored_at >= start_date)
    if end_date:
        query = query.filter(McpLlmAxisScore.scored_at <= end_date)

    # Execute query and format results
    scores = query.all()
    formatted_scores = [
        AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            scored_at=score.scored_at
        )
        for score in scores
    ]

    return ServerHistoryResponse(
        server_id=server_id,
        scores=formatted_scores
    )

@router.get("/{server_id}/history", response_model=ServerHistoryResponse)
async def server_history(
    server_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_session)
):
    try:
        # Parse date strings if provided
        parsed_start = datetime.fromisoformat(start_date) if start_date else None
        parsed_end = datetime.fromisoformat(end_date) if end_date else None

        return get_server_history(server_id, parsed_start, parsed_end, session)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server1 = McpServerRegistry(server_id="server1", name="Test Server 1")
    test_server2 = McpServerRegistry(server_id="server2", name="Test Server 2")
    test_session.add_all([test_server1, test_server2])

    test_scores = [
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis1",
            label="Label 1",
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            scored_at=datetime(2023, 1, 1)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis2",
            label="Label 2",
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6,
            scored_at=datetime(2023, 1, 2)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis3",
            label="Label 3",
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            scored_at=datetime(2023, 1, 3)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis1",
            label="Label 1",
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            scored_at=datetime(2023, 1, 1)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis2",
            label="Label 2",
            p_top=0.5,
            p_critical=0.4,
            p_danger=0.3,
            scored_at=datetime(2023, 1, 2)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis3",
            label="Label 3",
            p_top=0.4,
            p_critical=0.3,
            p_danger=0.2,
            scored_at=datetime(2023, 1, 3)
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Run tests
    client = TestClient(app)

    # Test server1 history
    response = client.get("/api/servers/server1/history")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "server1"
    assert len(data["scores"]) == 3
    assert data["scores"][0]["axis_name"] == "axis1"
    assert data["scores"][0]["p_top"] == 0.9

    # Test server2 history with date range
    response = client.get("/api/servers/server2/history?start_date=2023-01-01&end_date=2023-01-02")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "server2"
    assert len(data["scores"]) == 2
    assert data["scores"][0]["axis_name"] == "axis1"
    assert data["scores"][1]["axis_name"] == "axis2"

    # Test non-existent server
    response = client.get("/api/servers/nonexistent/history")
    assert response.status_code == 404

    print("PASS")