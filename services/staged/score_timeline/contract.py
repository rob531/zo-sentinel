from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter(prefix="/api/servers")

class AxisScore(BaseModel):
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

def get_risk_tier(score: float) -> str:
    if score >= 0.9:
        return "High"
    elif score >= 0.7:
        return "Medium"
    elif score >= 0.5:
        return "Low"
    else:
        return "Negligible"

def compute_snapshots(db: Session, server_id: str, days: int) -> List[Snapshot]:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get all scores for the server in the date range
    scores = db.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.axis,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.overall_risk
    ).join(
        McpServerRegistry,
        McpServerRegistry.id == McpLlmAxisScore.server_id
    ).filter(
        McpServerRegistry.id == server_id,
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).all()

    # Group scores by date
    scores_by_date = {}
    for score in scores:
        date = score.scored_at.date()
        if date not in scores_by_date:
            scores_by_date[date] = []
        scores_by_date[date].append(score)

    # Compute snapshots
    snapshots = []
    for date, scores in scores_by_date.items():
        # Get the latest score for each axis
        latest_scores = {}
        for score in scores:
            if score.axis not in latest_scores or score.scored_at > latest_scores[score.axis].scored_at:
                latest_scores[score.axis] = score

        # Compute overall score and risk tier
        overall_score = sum(score.p_top for score in latest_scores.values()) / len(latest_scores)
        risk_tier = get_risk_tier(overall_score)

        # Create snapshot
        axes = {score.axis: score.p_top for score in latest_scores.values()}
        snapshots.append(Snapshot(
            date=date.isoformat(),
            overall_score=overall_score,
            risk_tier=risk_tier,
            axes=axes
        ))

    # Sort snapshots by date
    snapshots.sort(key=lambda x: x.date)

    return snapshots

@router.get("/{server_id}/score/timeline", response_model=ScoreTimelineResponse)
async def get_score_timeline(
    server_id: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session)
):
    snapshots = compute_snapshots(db, server_id, days)
    return ScoreTimelineResponse(
        server_id=server_id,
        days=days,
        snapshots=snapshots
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    def seed_test_data():
        db = SessionLocal()
        try:
            # Create test servers
            server1 = McpServerRegistry(id="server1", name="Test Server 1")
            server2 = McpServerRegistry(id="server2", name="Test Server 2")
            db.add(server1)
            db.add(server2)

            # Create test scores
            now = datetime.utcnow()
            yesterday = now - timedelta(days=1)

            scores = [
                McpLlmAxisScore(
                    server_id="server1",
                    axis="security",
                    p_top=0.9,
                    overall_risk=0.9,
                    scored_at=yesterday - timedelta(hours=12)
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis="performance",
                    p_top=0.8,
                    overall_risk=0.8,
                    scored_at=yesterday - timedelta(hours=6)
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis="security",
                    p_top=0.85,
                    overall_risk=0.85,
                    scored_at=now - timedelta(hours=12)
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis="performance",
                    p_top=0.75,
                    overall_risk=0.75,
                    scored_at=now - timedelta(hours=6)
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis="security",
                    p_top=0.7,
                    overall_risk=0.7,
                    scored_at=yesterday - timedelta(hours=12)
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis="performance",
                    p_top=0.6,
                    overall_risk=0.6,
                    scored_at=yesterday - timedelta(hours=6)
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis="security",
                    p_top=0.65,
                    overall_risk=0.65,
                    scored_at=now - timedelta(hours=12)
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis="performance",
                    p_top=0.55,
                    overall_risk=0.55,
                    scored_at=now - timedelta(hours=6)
                ),
            ]
            db.add_all(scores)
            db.commit()
        finally:
            db.close()

    seed_test_data()

    # Run tests
    client = TestClient(app)

    response = client.get("/api/servers/server1/score/timeline")
    assert response.status_code == 200
    data = response.json()
    assert len(data["snapshots"]) >= 3
    assert any(snapshot["risk_tier"] for snapshot in data["snapshots"])

    print("PASS")