from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

class RiskTierHistoryItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    days: int
    history: List[RiskTierHistoryItem]

def get_risk_tier_history(server_id: str, days: int, session: Session) -> List[RiskTierHistoryItem]:
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get all risk tier changes for the server within the date range
    query = session.query(
        McpServerRegistry.last_assessed,
        McpServerRegistry.risk_tier
    ).filter(
        McpServerRegistry.server_id == server_id,
        McpServerRegistry.last_assessed >= cutoff_date
    ).order_by(
        McpServerRegistry.last_assessed.desc()
    ).all()

    # Group by date and count occurrences
    history = []
    for date, tier in query:
        date_str = date.isoformat()
        existing = next((item for item in history if item['date'] == date_str), None)
        if existing:
            existing['count'] += 1
        else:
            history.append({
                'date': date_str,
                'tier': tier,
                'count': 1
            })

    # Sort by date ascending (oldest first)
    history.sort(key=lambda x: x['date'])
    return history

@router.get("/servers/{server_id}/risk_tier_history", response_model=RiskTierHistoryResponse)
async def server_risk_tier_history(
    server_id: str,
    days: Optional[int] = 30,
    session: Session = Depends(get_session)
):
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="Days must be between 1 and 365")

    history = get_risk_tier_history(server_id, days, session)

    return {
        "server_id": server_id,
        "days": days,
        "history": history
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Create test app with dependency override
    test_app = FastAPI()
    test_app.include_router(router)

    async def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Insert test data
    with TestSession() as session:
        # Create test server
        test_server = McpServerRegistry(
            server_id="test-server-1",
            name="Test Server",
            risk_tier="low",
            last_assessed=datetime.utcnow() - timedelta(days=10),
            verdict="safe",
            confidence=0.9,
            trust_score=0.8
        )
        session.add(test_server)

        # Add some historical risk tier changes
        test_server.risk_tier = "medium"
        test_server.last_assessed = datetime.utcnow() - timedelta(days=5)
        session.add(test_server)

        test_server.risk_tier = "high"
        test_server.last_assessed = datetime.utcnow() - timedelta(days=2)
        session.add(test_server)

        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/servers/test-server-1/risk_tier_history?days=7")

    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["tier"] == "medium"
    assert data["history"][1]["tier"] == "high"
    print("PASS")