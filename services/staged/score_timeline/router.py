from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from .logic import get_score_timeline

router = APIRouter(prefix="/api")

class AxisScores(BaseModel):
    axis: str
    p_top: float

class TimelineSnapshot(BaseModel):
    date: str
    overall_score: float
    risk_tier: str
    axes: Dict[str, AxisScores]

class ScoreTimelineResponse(BaseModel):
    server_id: str
    days: int
    snapshots: List[TimelineSnapshot]

@router.get("/servers/{server_id}/score/timeline", response_model=ScoreTimelineResponse)
async def get_timeline(
    server_id: str,
    days: Optional[int] = 30,
    session: Session = Depends(get_session)
):
    try:
        timeline = get_score_timeline(session, server_id, days)
        return timeline
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        # Create test servers
        server1 = McpServerRegistry(
            server_id="server1",
            hostname="test1.example.com",
            org_id="org1",
            created_at=datetime.now()
        )
        server2 = McpServerRegistry(
            server_id="server2",
            hostname="test2.example.com",
            org_id="org2",
            created_at=datetime.now()
        )
        session.add_all([server1, server2])

        # Create test scores for server1
        now = datetime.now()
        scores1 = [
            McpLlmAxisScore(
                server_id="server1",
                axis="security",
                p_top=0.9,
                scored_at=now - timedelta(days=2),
                overall_risk=0.8
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis="performance",
                p_top=0.7,
                scored_at=now - timedelta(days=2),
                overall_risk=0.8
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis="security",
                p_top=0.8,
                scored_at=now - timedelta(days=1),
                overall_risk=0.7
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis="performance",
                p_top=0.6,
                scored_at=now - timedelta(days=1),
                overall_risk=0.7
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis="security",
                p_top=0.7,
                scored_at=now,
                overall_risk=0.6
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis="performance",
                p_top=0.5,
                scored_at=now,
                overall_risk=0.6
            )
        ]

        # Create test scores for server2
        scores2 = [
            McpLlmAxisScore(
                server_id="server2",
                axis="security",
                p_top=0.6,
                scored_at=now - timedelta(days=2),
                overall_risk=0.5
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis="performance",
                p_top=0.4,
                scored_at=now - timedelta(days=2),
                overall_risk=0.5
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis="security",
                p_top=0.5,
                scored_at=now - timedelta(days=1),
                overall_risk=0.4
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis="performance",
                p_top=0.3,
                scored_at=now - timedelta(days=1),
                overall_risk=0.4
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis="security",
                p_top=0.4,
                scored_at=now,
                overall_risk=0.3
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis="performance",
                p_top=0.2,
                scored_at=now,
                overall_risk=0.3
            )
        ]

        session.add_all(scores1 + scores2)
        session.commit()

    # Run test
    client = TestClient(app)
    response = client.get("/api/servers/server1/score/timeline?days=30")

    assert response.status_code == 200
    data = response.json()
    assert len(data["snapshots"]) >= 3
    assert "risk_tier" in data["snapshots"][0]

    print("PASS")