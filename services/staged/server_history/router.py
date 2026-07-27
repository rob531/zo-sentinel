from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import and_

router = APIRouter(prefix="/api")

class AxisScoreRecord(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: datetime

class ServerHistoryResponse(BaseModel):
    server_id: str
    records: List[AxisScoreRecord]

@router.get("/servers/{server_id}/history", response_model=ServerHistoryResponse)
async def get_server_history(
    server_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_session)
):
    try:
        # Validate server exists
        server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        # Parse dates if provided
        start = datetime.fromisoformat(start_date) if start_date else None
        end = datetime.fromisoformat(end_date) if end_date else None

        # Build query
        query = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server_id
        )

        if start:
            query = query.filter(McpLlmAxisScore.scored_at >= start)
        if end:
            query = query.filter(McpLlmAxisScore.scored_at <= end)

        # Execute query and format results
        results = query.all()
        records = [
            AxisScoreRecord(
                axis_name=score.axis_name,
                label=score.label,
                p_top=score.p_top,
                p_critical=score.p_critical,
                p_danger=score.p_danger,
                scored_at=score.scored_at
            )
            for score in results
        ]

        return ServerHistoryResponse(server_id=server_id, records=records)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

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
    test_session.add_all([
        McpServerRegistry(server_id="server1", name="Test Server 1"),
        McpServerRegistry(server_id="server2", name="Test Server 2"),
    ])

    test_scores = [
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis1",
            label="label1",
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            scored_at=datetime(2023, 1, 1, 12, 0)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis2",
            label="label2",
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6,
            scored_at=datetime(2023, 1, 2, 12, 0)
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis3",
            label="label3",
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            scored_at=datetime(2023, 1, 3, 12, 0)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis1",
            label="label1",
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            scored_at=datetime(2023, 1, 1, 12, 0)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis2",
            label="label2",
            p_top=0.5,
            p_critical=0.4,
            p_danger=0.3,
            scored_at=datetime(2023, 1, 2, 12, 0)
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis3",
            label="label3",
            p_top=0.4,
            p_critical=0.3,
            p_danger=0.2,
            scored_at=datetime(2023, 1, 3, 12, 0)
        ),
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Run tests
    client = TestClient(app)

    # Test server1 with no date filters
    response = client.get("/api/servers/server1/history")
    assert response.status_code == 200
    assert len(response.json()["records"]) == 3

    # Test server2 with date filter
    response = client.get("/api/servers/server2/history?start_date=2023-01-02")
    assert response.status_code == 200
    assert len(response.json()["records"]) == 2

    # Test non-existent server
    response = client.get("/api/servers/server3/history")
    assert response.status_code == 404

    print("PASS")