from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

router = APIRouter()

class ServerScoringFreshnessResponse(BaseModel):
    server_id: str
    last_scored_at: Optional[datetime]
    hours_since_last_score: Optional[float]
    avg_scoring_interval_hours: Optional[float]
    is_stale: bool
    next_expected_at_approx: Optional[datetime]

def calculate_freshness_stats(server_id: str, session: Session) -> ServerScoringFreshnessResponse:
    # Get last scored timestamp
    last_score = session.query(
        func.max(McpLlmAxisScores.scored_at).label('last_scored_at')
    ).filter(
        McpLlmAxisScores.server_id == server_id
    ).first()

    last_scored_at = last_score.last_scored_at if last_score.last_scored_at else None

    # Calculate hours since last score
    hours_since_last_score = None
    if last_scored_at:
        hours_since_last_score = (datetime.utcnow() - last_scored_at).total_seconds() / 3600

    # Calculate average scoring interval
    avg_interval = session.query(
        func.avg(
            (McpLlmAxisScores.scored_at - func.lag(McpLlmAxisScores.scored_at)
             .over(order_by=McpLlmAxisScores.scored_at)).label('interval')
        ).label('avg_interval')
    ).filter(
        McpLlmAxisScores.server_id == server_id
    ).first()

    avg_scoring_interval_hours = avg_interval.avg_interval.total_seconds() / 3600 if avg_interval.avg_interval else None

    # Determine if stale
    is_stale = hours_since_last_score is not None and hours_since_last_score > 168

    # Calculate next expected scoring time
    next_expected_at_approx = None
    if avg_scoring_interval_hours and last_scored_at:
        next_expected_at_approx = last_scored_at + timedelta(hours=avg_scoring_interval_hours)

    return ServerScoringFreshnessResponse(
        server_id=server_id,
        last_scored_at=last_scored_at,
        hours_since_last_score=hours_since_last_score,
        avg_scoring_interval_hours=avg_scoring_interval_hours,
        is_stale=is_stale,
        next_expected_at_approx=next_expected_at_approx
    )

@router.get("/servers/{server_id}/scoring-freshness", response_model=ServerScoringFreshnessResponse)
async def get_server_scoring_freshness(
    server_id: str,
    session: Session = Depends(get_session)
):
    # Verify server exists
    if not session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first():
        raise HTTPException(status_code=404, detail="Server not found")

    return calculate_freshness_stats(server_id, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = McpServerRegistry(server_id="test-server", confidence=0.9)
    test_session.add(test_server)
    test_session.commit()

    # Add some test scores
    now = datetime.utcnow()
    test_session.add_all([
        McpLlmAxisScores(
            server_id="test-server",
            scored_at=now - timedelta(hours=24),
            axis="test-axis",
            score=0.8
        ),
        McpLlmAxisScores(
            server_id="test-server",
            scored_at=now - timedelta(hours=48),
            axis="test-axis",
            score=0.7
        ),
        McpLlmAxisScores(
            server_id="test-server",
            scored_at=now - timedelta(hours=72),
            axis="test-axis",
            score=0.6
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test-server/scoring-freshness")
    assert response.status_code == 200
    data = response.json()
    assert "is_stale" in data
    assert isinstance(data["hours_since_last_score"], float) and data["hours_since_last_score"] > 0

    print("PASS")