from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

class AxisScores(BaseModel):
    axis: str
    p_top: float

class Snapshot(BaseModel):
    date: str
    overall_score: float
    risk_tier: str
    axes: Dict[str, float]

class ScoreTimelineResponse(BaseModel):
    server_id: str
    days: int
    snapshots: List[Snapshot]

def get_score_timeline(server_id: str, days: int = 30, session: Session = Depends(get_session)) -> ScoreTimelineResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get all scores for the server within the date range
    scores = session.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.axis,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.overall_risk
    ).join(
        McpServerRegistry,
        McpServerRegistry.id == McpLlmAxisScore.server_id
    ).filter(
        and_(
            McpServerRegistry.id == server_id,
            McpLlmAxisScore.scored_at >= start_date,
            McpLlmAxisScore.scored_at <= end_date
        )
    ).all()

    # Group scores by date
    scores_by_date = {}
    for score in scores:
        date = score.scored_at.date()
        if date not in scores_by_date:
            scores_by_date[date] = []
        scores_by_date[date].append(score)

    # Create snapshots for each date
    snapshots = []
    for date, date_scores in scores_by_date.items():
        axes = {}
        overall_score = 0
        count = 0

        for score in date_scores:
            axes[score.axis] = score.p_top
            overall_score += score.overall_risk
            count += 1

        if count > 0:
            overall_score /= count
            risk_tier = _determine_risk_tier(overall_score)
            snapshots.append(Snapshot(
                date=date.isoformat(),
                overall_score=overall_score,
                risk_tier=risk_tier,
                axes=axes
            ))

    # Sort snapshots by date
    snapshots.sort(key=lambda x: x.date)

    return ScoreTimelineResponse(
        server_id=server_id,
        days=days,
        snapshots=snapshots
    )

def _determine_risk_tier(score: float) -> str:
    if score >= 0.9:
        return "High"
    elif score >= 0.7:
        return "Medium"
    elif score >= 0.5:
        return "Low"
    else:
        return "Negligible"

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        server1 = McpServerRegistry(id="server1", name="Test Server 1")
        server2 = McpServerRegistry(id="server2", name="Test Server 2")
        session.add(server1)
        session.add(server2)

        # Create test scores
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)

        scores = [
            McpLlmAxisScore(
                server_id="server1",
                scored_at=now,
                axis="axis1",
                p_top=0.8,
                overall_risk=0.7
            ),
            McpLlmAxisScore(
                server_id="server1",
                scored_at=now,
                axis="axis2",
                p_top=0.6,
                overall_risk=0.6
            ),
            McpLlmAxisScore(
                server_id="server1",
                scored_at=yesterday,
                axis="axis1",
                p_top=0.7,
                overall_risk=0.6
            ),
            McpLlmAxisScore(
                server_id="server2",
                scored_at=now,
                axis="axis1",
                p_top=0.9,
                overall_risk=0.8
            ),
            McpLlmAxisScore(
                server_id="server2",
                scored_at=now,
                axis="axis2",
                p_top=0.5,
                overall_risk=0.5
            ),
            McpLlmAxisScore(
                server_id="server2",
                scored_at=yesterday,
                axis="axis1",
                p_top=0.8,
                overall_risk=0.7
            ),
        ]
        session.add_all(scores)
        session.commit()

        # Test the function
        response = get_score_timeline("server1", days=2)
        assert response.server_id == "server1"
        assert response.days == 2
        assert len(response.snapshots) >= 2
        assert any(snapshot.risk_tier for snapshot in response.snapshots)

        print("PASS")
    finally:
        session.close()