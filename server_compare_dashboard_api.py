from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from typing import List, Optional

router = APIRouter()

class ServerComparison(BaseModel):
    server1_id: int
    server2_id: int
    risk_tier_comparison: str
    overall_risk_comparison: str
    axis_scores_comparison: List[dict]

@router.get("/servers/compare", response_model=ServerComparison)
async def compare_servers(
    server1_id: int,
    server2_id: int,
    db: Session = Depends(get_session)
):
    # Get server data from registry
    server1 = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == server1_id).first()
    server2 = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == server2_id).first()

    if not server1 or not server2:
        raise HTTPException(status_code=404, detail="One or both servers not found")

    # Get axis scores for both servers
    scores1 = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server1_id).all()
    scores2 = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server2_id).all()

    if not scores1 or not scores2:
        raise HTTPException(status_code=404, detail="Scores not found for one or both servers")

    # Calculate comparisons
    risk_tier_comparison = f"{server1.risk_tier} vs {server2.risk_tier}"
    overall_risk_comparison = f"{server1.overall_risk} vs {server2.overall_risk}"

    axis_scores_comparison = []
    for axis in ["security", "privacy", "reliability", "performance"]:
        score1 = next((s.score for s in scores1 if s.axis == axis), None)
        score2 = next((s.score for s in scores2 if s.axis == axis), None)
        if score1 is not None and score2 is not None:
            axis_scores_comparison.append({
                "axis": axis,
                "server1_score": score1,
                "server2_score": score2
            })

    return ServerComparison(
        server1_id=server1_id,
        server2_id=server2_id,
        risk_tier_comparison=risk_tier_comparison,
        overall_risk_comparison=overall_risk_comparison,
        axis_scores_comparison=axis_scores_comparison
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    # Create test data
    test_session = TestingSessionLocal()
    test_server1 = MCPServerRegistry(
        id=1,
        name="Test Server 1",
        risk_tier="High",
        overall_risk=85.5
    )
    test_server2 = MCPServerRegistry(
        id=2,
        name="Test Server 2",
        risk_tier="Medium",
        overall_risk=65.3
    )
    test_session.add_all([test_server1, test_server2])

    test_scores1 = [
        MCPLLMAxisScores(server_id=1, axis="security", score=90.0),
        MCPLLMAxisScores(server_id=1, axis="privacy", score=85.0),
        MCPLLMAxisScores(server_id=1, axis="reliability", score=80.0),
        MCPLLMAxisScores(server_id=1, axis="performance", score=88.0)
    ]
    test_scores2 = [
        MCPLLMAxisScores(server_id=2, axis="security", score=75.0),
        MCPLLMAxisScores(server_id=2, axis="privacy", score=65.0),
        MCPLLMAxisScores(server_id=2, axis="reliability", score=70.0),
        MCPLLMAxisScores(server_id=2, axis="performance", score=60.0)
    ]
    test_session.add_all(test_scores1 + test_scores2)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/compare?server1_id=1&server2_id=2")

    if response.status_code == 200 and response.json():
        print("PASS")
    else:
        print("FAIL")