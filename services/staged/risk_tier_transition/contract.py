from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy import func, and_

router = APIRouter(prefix="/api/risk/tier/transition")

class TierTransition(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierTransitionResponse(BaseModel):
    server_id: str
    days: int
    transitions: List[TierTransition]

def determine_tier(p_top: float) -> str:
    if p_top >= 0.8:
        return "HIGH"
    elif p_top >= 0.5:
        return "MEDIUM"
    else:
        return "LOW"

@router.get("/",
            response_model=RiskTierTransitionResponse,
            responses={200: {"description": "Returns per-day risk tier transitions"}},
            tags=["risk_tier_transition"])
async def get_risk_tier_transitions(server_id: str, days: int, session=Depends(get_session)):
    if days <= 0:
        raise HTTPException(status_code=400, detail="Days must be a positive integer")

    start_date = datetime.now() - timedelta(days=days)

    query = session.query(
        func.date(McpLlmAxisScore.scored_at).label('date'),
        func.count().label('count'),
        func.avg(McpLlmAxisScore.p_top).label('avg_p_top')
    ).join(
        McpServerRegistry, McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).filter(
        and_(
            McpServerRegistry.server_id == server_id,
            McpLlmAxisScore.scored_at >= start_date
        )
    ).group_by(
        func.date(McpLlmAxisScore.scored_at)
    )

    results = query.all()

    transitions = []
    for row in results:
        tier = determine_tier(row.avg_p_top)
        transitions.append({
            "date": row.date.isoformat(),
            "tier": tier,
            "count": row.count
        })

    return {
        "server_id": server_id,
        "days": days,
        "transitions": transitions
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal, Base
    from app.models import McpLlmAxisScore, McpServerRegistry
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_session = SessionLocal()
    test_server = McpServerRegistry(server_id="test_server", name="Test Server")
    test_session.add(test_server)
    test_session.commit()

    # Add scores for two different dates
    test_session.add(McpLlmAxisScore(
        server_id="test_server",
        scored_at=datetime.now() - timedelta(days=1),
        p_top=0.9
    ))
    test_session.add(McpLlmAxisScore(
        server_id="test_server",
        scored_at=datetime.now() - timedelta(days=2),
        p_top=0.4
    ))
    test_session.commit()

    # Create a test client
    client = TestClient(router)

    # Test the endpoint
    response = client.get("/api/risk/tier/transition?server_id=test_server&days=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["transitions"]) == 2
    assert data["transitions"][0]["tier"] == "HIGH"
    assert data["transitions"][1]["tier"] == "LOW"

    print("PASS")