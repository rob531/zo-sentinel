from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPLLMAxisScore
from sqlalchemy.orm import Session
import httpx

router = APIRouter()

class RiskTierHistoryItem(BaseModel):
    date: str
    risk_tier: str
    overall_risk: float

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    history: List[RiskTierHistoryItem]

def get_risk_tier_history(server_id: str, session: Session = Depends(get_session)) -> RiskTierHistoryResponse:
    # Calculate the date 90 days ago from today
    ninety_days_ago = datetime.now() - timedelta(days=90)

    # Query the database for the risk tier history
    results = session.query(
        MCPLLMAxisScore.scored_at,
        MCPLLMAxisScore.risk_tier,
        MCPLLMAxisScore.overall_risk
    ).filter(
        MCPLLMAxisScore.server_id == server_id,
        MCPLLMAxisScore.scored_at >= ninety_days_ago
    ).order_by(
        MCPLLMAxisScore.scored_at.desc()
    ).all()

    # Format the results
    history = [
        RiskTierHistoryItem(
            date=result.scored_at.strftime('%Y-%m-%d'),
            risk_tier=result.risk_tier,
            overall_risk=result.overall_risk
        )
        for result in results
    ]

    return RiskTierHistoryResponse(server_id=server_id, history=history)

@router.get("/servers/{server_id}/risk-tier-history", response_model=RiskTierHistoryResponse)
async def get_server_risk_tier_history(server_id: str, session: Session = Depends(get_session)):
    return get_risk_tier_history(server_id, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the get_session dependency for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    test_server_id = "test-id"
    test_data = [
        {"scored_at": datetime.now() - timedelta(days=1), "risk_tier": "high", "overall_risk": 0.9},
        {"scored_at": datetime.now() - timedelta(days=2), "risk_tier": "medium", "overall_risk": 0.6},
        {"scored_at": datetime.now() - timedelta(days=3), "risk_tier": "low", "overall_risk": 0.3}
    ]

    with TestSessionLocal() as session:
        for data in test_data:
            session.add(MCPLLMAxisScore(
                server_id=test_server_id,
                scored_at=data["scored_at"],
                risk_tier=data["risk_tier"],
                overall_risk=data["overall_risk"]
            ))
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/servers/{test_server_id}/risk-tier-history")
    assert response.status_code == 200
    assert len(response.json()["history"]) > 0
    print("PASS")