from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores
from sqlalchemy.orm import Session
from datetime import datetime

router = APIRouter()

class ScoreEntry(BaseModel):
    scored_at: datetime
    p_top: float
    risk_tier: str
    model_version: str

class AxisScoreHistoryResponse(BaseModel):
    server_id: str
    axis: str
    entries: List[ScoreEntry]
    count: int

@router.get("/servers/{server_id}/axis-score-history", response_model=AxisScoreHistoryResponse)
async def get_axis_score_history(
    server_id: str,
    axis: str = Query("overall_risk"),
    limit: int = Query(30),
    direction: str = Query("desc"),
    db: Session = Depends(get_session)
):
    # Check if server exists
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Validate direction
    if direction not in ["asc", "desc"]:
        raise HTTPException(status_code=400, detail="Invalid direction. Must be 'asc' or 'desc'")

    # Get score history
    query = db.query(
        McpLlmAxisScores.scored_at,
        McpLlmAxisScores.p_top,
        McpLlmAxisScores.risk_tier,
        McpLlmAxisScores.model_version
    ).filter(
        McpLlmAxisScores.server_id == server_id,
        McpLlmAxisScores.axis_name == axis
    ).order_by(
        McpLlmAxisScores.scored_at.desc() if direction == "desc" else McpLlmAxisScores.scored_at.asc()
    ).limit(limit)

    entries = [ScoreEntry(**row._asdict()) for row in query.all()]
    count = len(entries)

    return AxisScoreHistoryResponse(
        server_id=server_id,
        axis=axis,
        entries=entries,
        count=count
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Mock data
    def get_mock_session():
        session = SessionLocal()
        # Add mock server
        session.add(McpServerRegistry(server_id="test_server"))
        # Add mock scores
        session.add(McpLlmAxisScores(
            server_id="test_server",
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 1),
            p_top=0.8,
            risk_tier="high",
            model_version="v1"
        ))
        session.add(McpLlmAxisScores(
            server_id="test_server",
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 2),
            p_top=0.7,
            risk_tier="medium",
            model_version="v1"
        ))
        session.add(McpLlmAxisScores(
            server_id="test_server",
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 3),
            p_top=0.6,
            risk_tier="low",
            model_version="v1"
        ))
        session.commit()
        return session

    # Override dependency for testing
    app.dependency_overrides[get_session] = get_mock_session

    # Test
    client = TestClient(app)
    response = client.get("/servers/test_server/axis-score-history")
    assert response.status_code == 200
    data = response.json()
    assert len(data["entries"]) == 3
    for entry in data["entries"]:
        assert "scored_at" in entry
        assert "p_top" in entry
        assert "risk_tier" in entry
    print("PASS")