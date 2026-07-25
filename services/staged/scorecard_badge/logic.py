from typing import Optional
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel

class ScorecardBadgeResponse(BaseModel):
    server_id: str
    badge: str
    composite_score: float
    top_risk_axis: Optional[str]
    axis_count: int
    scored_at: str

def get_scorecard_badge(server_id: str, session: Session = Depends(get_session)) -> ScorecardBadgeResponse:
    # Query the database for the server's risk scores
    query = session.query(
        McpServerRegistry.server_id,
        McpLlmAxisScore.axis_1_score,
        McpLlmAxisScore.axis_2_score,
        McpLlmAxisScore.axis_3_score,
        McpLlmAxisScore.axis_4_score,
        McpLlmAxisScore.axis_5_score,
        McpLlmAxisScore.axis_6_score,
        McpLlmAxisScore.axis_7_score,
        McpLlmAxisScore.overall_risk,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_critical,
        McpLlmAxisScore.scored_at
    ).join(
        McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not query:
        raise HTTPException(status_code=404, detail="Server not found")

    # Extract scores from the query result
    scores = {
        'axis_1': query.axis_1_score,
        'axis_2': query.axis_2_score,
        'axis_3': query.axis_3_score,
        'axis_4': query.axis_4_score,
        'axis_5': query.axis_5_score,
        'axis_6': query.axis_6_score,
        'axis_7': query.axis_7_score
    }

    # Count the number of scored axes
    axis_count = sum(1 for score in scores.values() if score is not None)

    # Calculate composite score (average of all axis scores)
    valid_scores = [score for score in scores.values() if score is not None]
    composite_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0.0

    # Determine the top risk axis (axis with the lowest score)
    top_risk_axis = min(scores.items(), key=lambda x: x[1] if x[1] is not None else float('inf'))[0] if valid_scores else None

    # Determine the badge based on the scores
    if axis_count < 4:
        badge = "INSUFFICIENT"
    elif all(score >= 70 for score in valid_scores) and query.p_critical <= 0.3:
        badge = "TRUSTED"
    elif any(score < 50 for score in valid_scores) or query.p_critical > 0.3:
        badge = "CAUTION"
    elif any(score < 30 for score in valid_scores) or query.p_critical > 0.6:
        badge = "HIGH_RISK"
    else:
        badge = "CAUTION"

    return ScorecardBadgeResponse(
        server_id=server_id,
        badge=badge,
        composite_score=composite_score,
        top_risk_axis=top_risk_axis,
        axis_count=axis_count,
        scored_at=query.scored_at.isoformat() if query.scored_at else None
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from datetime import datetime

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        server1 = McpServerRegistry(server_id="server1", name="Test Server 1")
        server2 = McpServerRegistry(server_id="server2", name="Test Server 2")
        server3 = McpServerRegistry(server_id="server3", name="Test Server 3")
        session.add_all([server1, server2, server3])

        # Create test scores
        score1 = McpLlmAxisScore(
            server_id="server1",
            axis_1_score=80,
            axis_2_score=85,
            axis_3_score=90,
            axis_4_score=75,
            axis_5_score=82,
            axis_6_score=78,
            axis_7_score=88,
            overall_risk=0.1,
            p_top=0.9,
            p_critical=0.1,
            scored_at=datetime.now()
        )
        score2 = McpLlmAxisScore(
            server_id="server2",
            axis_1_score=60,
            axis_2_score=45,
            axis_3_score=55,
            axis_4_score=70,
            axis_5_score=65,
            axis_6_score=50,
            axis_7_score=60,
            overall_risk=0.3,
            p_top=0.7,
            p_critical=0.4,
            scored_at=datetime.now()
        )
        score3 = McpLlmAxisScore(
            server_id="server3",
            axis_1_score=20,
            axis_2_score=None,
            axis_3_score=None,
            axis_4_score=None,
            axis_5_score=None,
            axis_6_score=None,
            axis_7_score=None,
            overall_risk=0.8,
            p_top=0.2,
            p_critical=0.7,
            scored_at=datetime.now()
        )
        session.add_all([score1, score2, score3])
        session.commit()

        # Test the function
        result1 = get_scorecard_badge("server1")
        result2 = get_scorecard_badge("server2")
        result3 = get_scorecard_badge("server3")

        assert result1.badge == "TRUSTED"
        assert result2.badge == "CAUTION"
        assert result3.badge == "INSUFFICIENT"

        print("PASS")
    finally:
        session.close()