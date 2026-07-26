from fastapi import Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func

class Transition(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierTransitionResponse(BaseModel):
    server_id: str
    days: int
    transitions: List[Transition]

def determine_tier(p_top: float) -> str:
    if p_top >= 0.8:
        return "HIGH"
    elif p_top >= 0.5:
        return "MEDIUM"
    else:
        return "LOW"

def get_risk_tier_transitions(
    server_id: str,
    days: int,
    db: Session = Depends(get_session)
) -> RiskTierTransitionResponse:
    # Validate server exists
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Calculate date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Query scores in date range
    scores = db.query(
        func.date(McpLlmAxisScore.scored_at).label('date'),
        McpLlmAxisScore.p_top
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).all()

    # Group by date and determine tier
    transitions = []
    for date, p_top in scores:
        tier = determine_tier(p_top)
        # Find existing entry for this date or create new
        existing = next((t for t in transitions if t['date'] == date.strftime('%Y-%m-%d')), None)
        if existing:
            existing['count'] += 1
        else:
            transitions.append({
                'date': date.strftime('%Y-%m-%d'),
                'tier': tier,
                'count': 1
            })

    return RiskTierTransitionResponse(
        server_id=server_id,
        days=days,
        transitions=transitions
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test route
    from fastapi import APIRouter
    router = APIRouter()
    router.get("/api/risk/tier/transition")(get_risk_tier_transitions)
    app.include_router(router)

    # Seed test data
    test_session = TestSession()
    test_server = McpServerRegistry(server_id="test_server")
    test_session.add(test_server)
    test_session.commit()

    # Add scores for two dates
    test_session.add(McpLlmAxisScore(
        server_id="test_server",
        scored_at=datetime(2023, 1, 1),
        p_top=0.9
    ))
    test_session.add(McpLlmAxisScore(
        server_id="test_server",
        scored_at=datetime(2023, 1, 1),
        p_top=0.9
    ))
    test_session.add(McpLlmAxisScore(
        server_id="test_server",
        scored_at=datetime(2023, 1, 2),
        p_top=0.4
    ))
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier/transition?server_id=test_server&days=3")

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test_server"
    assert data["days"] == 3
    assert len(data["transitions"]) == 2

    # Verify transition counts
    high_transitions = [t for t in data["transitions"] if t["tier"] == "HIGH"]
    assert len(high_transitions) == 1
    assert high_transitions[0]["count"] == 2

    low_transitions = [t for t in data["transitions"] if t["tier"] == "LOW"]
    assert len(low_transitions) == 1
    assert low_transitions[0]["count"] == 1

    print("PASS")