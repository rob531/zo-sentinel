from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScore
from typing import List, Dict

router = APIRouter()

def get_risk_tier_summary(db: Session = Depends(get_session)) -> Dict[str, int]:
    """
    Aggregates risk tier counts across all MCP servers by grouping by risk_tier
    and counting servers in each tier.
    """
    results = db.query(
        MCPLLMAxisScore.risk_tier,
        MCPLLMAxisScore.server_id
    ).group_by(
        MCPLLMAxisScore.risk_tier
    ).all()

    summary = {}
    for tier, _ in results:
        count = db.query(MCPLLMAxisScore).filter(
            MCPLLMAxisScore.risk_tier == tier
        ).count()
        summary[tier] = count

    return summary

@router.get("/risk_tier_summary", response_model=Dict[str, int])
async def risk_tier_summary() -> Dict[str, int]:
    return get_risk_tier_summary()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    from app.models import MCPLLMAxisScore
    test_session = TestSession()
    test_session.add_all([
        MCPLLMAxisScore(server_id=1, risk_tier="TRUSTED_GENERAL"),
        MCPLLMAxisScore(server_id=2, risk_tier="TRUSTED_GENERAL"),
        MCPLLMAxisScore(server_id=3, risk_tier="HIGH_RISK_ISOLATED"),
        MCPLLMAxisScore(server_id=4, risk_tier="HIGH_RISK_ISOLATED"),
        MCPLLMAxisScore(server_id=5, risk_tier="HIGH_RISK_ISOLATED"),
    ])
    test_session.commit()

    client = TestClient(app)
    response = client.get("/risk_tier_summary")
    assert response.status_code == 200
    result = response.json()
    assert "TRUSTED_GENERAL" in result and isinstance(result["TRUSTED_GENERAL"], int)
    assert "HIGH_RISK_ISOLATED" in result and isinstance(result["HIGH_RISK_ISOLATED"], int)
    print("PASS")