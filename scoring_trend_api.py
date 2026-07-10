from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class ScorePoint(BaseModel):
    scored_at: datetime
    p_top: float
    p_critical: float
    p_danger: float
    label: str
    escalated: bool

class AxisTimeline(BaseModel):
    axis_name: str
    timeline: List[ScorePoint]

class OverallScorePoint(BaseModel):
    scored_at: datetime
    overall_p_top: float

class ScoringTrendResponse(BaseModel):
    server_id: int
    axis_scores: List[AxisTimeline]
    score_trajectory: List[OverallScorePoint]
    as_of: datetime

@router.get("/servers/{server_id}/scoring-trend", response_model=ScoringTrendResponse)
async def get_scoring_trend(
    server_id: int,
    limit: Optional[int] = 100,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    session: Session = Depends(get_session)
):
    query = session.query(
        McpLlmAxisScores.server_id,
        McpLlmAxisScores.axis_name,
        McpLlmAxisScores.scored_at,
        McpLlmAxisScores.p_top,
        McpLlmAxisScores.p_critical,
        McpLlmAxisScores.p_danger,
        McpLlmAxisScores.label,
        McpLlmAxisScores.escalated
    ).filter(
        McpLlmAxisScores.server_id == server_id
    )

    if start_date:
        query = query.filter(McpLlmAxisScores.scored_at >= start_date)
    if end_date:
        query = query.filter(McpLlmAxisScores.scored_at <= end_date)

    query = query.order_by(desc(McpLlmAxisScores.scored_at)).limit(limit)

    results = query.all()

    if not results:
        raise HTTPException(status_code=404, detail="No scoring data found for this server")

    axis_scores_map = {}
    score_trajectory = []

    for row in results:
        axis_name = row.axis_name
        if axis_name not in axis_scores_map:
            axis_scores_map[axis_name] = []

        axis_scores_map[axis_name].append(ScorePoint(
            scored_at=row.scored_at,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            label=row.label,
            escalated=row.escalated
        ))

        # For overall score trajectory, we'll use the first axis's p_top as overall_p_top
        if not score_trajectory or score_trajectory[-1].scored_at != row.scored_at:
            score_trajectory.append(OverallScorePoint(
                scored_at=row.scored_at,
                overall_p_top=row.p_top
            ))

    axis_scores = [
        AxisTimeline(axis_name=axis_name, timeline=timeline)
        for axis_name, timeline in axis_scores_map.items()
    ]

    as_of = max(row.scored_at for row in results)

    return ScoringTrendResponse(
        server_id=server_id,
        axis_scores=axis_scores,
        score_trajectory=score_trajectory,
        as_of=as_of
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session
    from datetime import datetime, timedelta

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Add test data
    test_session = TestSession()
    test_server_id = 1
    now = datetime.now()
    test_data = [
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="security",
            scored_at=now - timedelta(days=2),
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            label="high",
            escalated=True
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="security",
            scored_at=now - timedelta(days=1),
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6,
            label="medium",
            escalated=False
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="performance",
            scored_at=now,
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            label="low",
            escalated=False
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Run test
    client = TestClient(app)
    response = client.get(f"/servers/{test_server_id}/scoring-trend")

    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == test_server_id
    assert len(data["axis_scores"]) >= 1
    assert len(data["score_trajectory"]) >= 1

    print("PASS")