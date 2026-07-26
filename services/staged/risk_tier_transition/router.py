from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/risk/tier/transition")

class Transition(BaseModel):
    date: str
    tier: str
    count: int

class Response(BaseModel):
    server_id: str
    days: int
    transitions: List[Transition]

def get_risk_tier(p_top: float) -> str:
    if p_top >= 0.8:
        return "HIGH"
    elif p_top >= 0.5:
        return "MEDIUM"
    else:
        return "LOW"

@router.get("/", response_model=Response)
async def get_risk_tier_transitions(
    server_id: str,
    days: int,
    session: Session = Depends(get_session)
):
    if days <= 0:
        raise HTTPException(status_code=400, detail="Days must be positive")

    start_date = datetime.now() - timedelta(days=days)

    query = (
        session.query(
            func.date(McpLlmAxisScore.scored_at).label("date"),
            func.count().label("count"),
            func.max(McpLlmAxisScore.p_top).label("p_top")
        )
        .join(McpServerRegistry, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .filter(
            and_(
                McpServerRegistry.server_id == server_id,
                McpLlmAxisScore.scored_at >= start_date
            )
        )
        .group_by(func.date(McpLlmAxisScore.scored_at))
        .all()
    )

    transitions = []
    for row in query:
        tier = get_risk_tier(row.p_top)
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
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create test data
    test_server_id = "test_server_123"
    test_date1 = datetime.now() - timedelta(days=1)
    test_date2 = datetime.now() - timedelta(days=2)

    test_client = TestClient(app)

    # Insert test data
    with SessionLocal() as session:
        session.add(McpServerRegistry(server_id=test_server_id))
        session.add(McpLlmAxisScore(
            server_id=test_server_id,
            scored_at=test_date1,
            p_top=0.9
        ))
        session.add(McpLlmAxisScore(
            server_id=test_server_id,
            scored_at=test_date2,
            p_top=0.4
        ))
        session.commit()

    # Test the endpoint
    response = test_client.get(f"/api/risk/tier/transition?server_id={test_server_id}&days=3")

    assert response.status_code == 200
    data = response.json()
    assert len(data["transitions"]) == 2
    assert data["transitions"][0]["tier"] == "HIGH"
    assert data["transitions"][1]["tier"] == "LOW"

    print("PASS")