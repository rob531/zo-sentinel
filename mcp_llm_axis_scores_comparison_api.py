from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScores
from datetime import datetime

router = APIRouter()

class AxisScoreComparison(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: datetime

class ServerComparison(BaseModel):
    server_a: Dict[str, AxisScoreComparison]
    server_b: Dict[str, AxisScoreComparison]
    deltas: Dict[str, float]

@router.get("/servers/{server_id_a}/compare/{server_id_b}", response_model=ServerComparison)
async def compare_axis_scores(
    server_id_a: int,
    server_id_b: int,
    axes: Optional[str] = None,
    session: Session = Depends(get_session)
):
    # Default to all 7 axes if none specified
    all_axes = [
        "overall_risk", "auth_strength", "data_safety",
        "privacy_compliance", "content_moderation",
        "performance_reliability", "community_trust"
    ]
    requested_axes = axes.split(",") if axes else all_axes

    # Validate requested axes
    for axis in requested_axes:
        if axis not in all_axes:
            raise HTTPException(status_code=400, detail=f"Invalid axis: {axis}")

    # Get latest scores for server_a
    server_a_scores = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id_a,
        MCPLLMAxisScores.axis.in_(requested_axes)
    ).order_by(MCPLLMAxisScores.scored_at.desc()).all()

    # Get latest scores for server_b
    server_b_scores = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id_b,
        MCPLLMAxisScores.axis.in_(requested_axes)
    ).order_by(MCPLLMAxisScores.scored_at.desc()).all()

    # Group scores by axis
    def group_scores(scores):
        grouped = {}
        for score in scores:
            if score.axis not in grouped or score.scored_at > grouped[score.axis].scored_at:
                grouped[score.axis] = score
        return grouped

    server_a_grouped = group_scores(server_a_scores)
    server_b_grouped = group_scores(server_b_scores)

    # Prepare response
    response = {
        "server_a": {},
        "server_b": {},
        "deltas": {}
    }

    for axis in requested_axes:
        a_score = server_a_grouped.get(axis)
        b_score = server_b_grouped.get(axis)

        if not a_score or not b_score:
            continue

        response["server_a"][axis] = {
            "label": a_score.axis,
            "p_top": a_score.p_top,
            "p_critical": a_score.p_critical,
            "p_danger": a_score.p_danger,
            "scored_at": a_score.scored_at
        }

        response["server_b"][axis] = {
            "label": b_score.axis,
            "p_top": b_score.p_top,
            "p_critical": b_score.p_critical,
            "p_danger": b_score.p_danger,
            "scored_at": b_score.scored_at
        }

        # Calculate delta (server_a - server_b)
        response["deltas"][axis] = (
            a_score.p_top - b_score.p_top +
            a_score.p_critical - b_score.p_critical +
            a_score.p_danger - b_score.p_danger
        )

    return response

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the session dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_data = [
        MCPLLMAxisScores(
            server_id=1,
            axis="overall_risk",
            p_top=0.8,
            p_critical=0.1,
            p_danger=0.1,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id=1,
            axis="auth_strength",
            p_top=0.7,
            p_critical=0.2,
            p_danger=0.1,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id=2,
            axis="overall_risk",
            p_top=0.6,
            p_critical=0.2,
            p_danger=0.2,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id=2,
            axis="auth_strength",
            p_top=0.5,
            p_critical=0.3,
            p_danger=0.2,
            scored_at=datetime.now()
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Run test
    client = TestClient(app)
    response = client.get("/servers/1/compare/2")
    assert response.status_code == 200
    data = response.json()

    # Verify all 7 axes are present (though we only seeded 2)
    assert len(data["server_a"]) == 2
    assert len(data["server_b"]) == 2
    assert len(data["deltas"]) == 2

    # Verify deltas are non-null
    for axis in data["deltas"]:
        assert data["deltas"][axis] is not None

    print("PASS")