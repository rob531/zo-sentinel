from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typing import List, Dict
from datetime import datetime
from app.db import get_session
from app.models import MCPLLMAxisScores
from pydantic import BaseModel

router = APIRouter()

class RiskTrendItem(BaseModel):
    timestamp: str
    risk_tier: str

class RiskTrendResponse(BaseModel):
    server_id: str
    trend: List[RiskTrendItem]

def get_server_risk_trend(server_id: str, session: Session = Depends(get_session)) -> Dict:
    scores = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis == 'overall_risk'
    ).order_by(MCPLLMAxisScores.scored_at).all()

    trend = []
    for score in scores:
        trend.append({
            "timestamp": score.scored_at.isoformat(),
            "risk_tier": score.risk_tier
        })

    return {
        "server_id": server_id,
        "trend": trend
    }

@router.get("/servers/{server_id}/risk-trend", response_model=RiskTrendResponse)
async def server_risk_trend(server_id: str, session: Session = Depends(get_session)):
    result = get_server_risk_trend(server_id, session)
    if not result["trend"]:
        raise HTTPException(status_code=404, detail="No risk trend data found for server")
    return result

if __name__ == '__main__':
    from app.db import Base, engine
    from app.models import MCPLLMAxisScores
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Add test data
    test_server_id = "test_server_123"
    test_scores = [
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis="overall_risk",
            scored_at=datetime(2023, 1, 1),
            risk_tier="low"
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis="overall_risk",
            scored_at=datetime(2023, 1, 15),
            risk_tier="medium"
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis="overall_risk",
            scored_at=datetime(2023, 2, 1),
            risk_tier="high"
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(router)
    response = client.get(f"/servers/{test_server_id}/risk-trend")
    assert response.status_code == 200
    assert len(response.json()["trend"]) > 0
    print("PASS")