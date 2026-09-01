from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

class RiskTierHistoryItem(BaseModel):
    date: str
    tier: str
    score: float

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    history: List[RiskTierHistoryItem]

def get_risk_tier(score: float) -> str:
    if score >= 0.9:
        return "High"
    elif score >= 0.7:
        return "Medium"
    elif score >= 0.5:
        return "Low"
    else:
        return "Negligible"

@router.get("/server/{server_id}/risk_tier_history", response_model=RiskTierHistoryResponse)
async def get_server_risk_tier_history(
    server_id: str,
    days: Optional[int] = 30,
    session: Session = Depends(get_session)
):
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get historical scores
    scores = session.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.p_top
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= cutoff_date
    ).order_by(
        McpLlmAxisScore.scored_at.desc()
    ).all()

    # Get current risk tier from server registry
    current_tier = session.query(
        McpServerRegistry.risk_tier
    ).filter(
        McpServerRegistry.server_id == server_id
    ).scalar()

    history = []
    for scored_at, score in scores:
        tier = get_risk_tier(score)
        history.append({
            "date": scored_at.isoformat(),
            "tier": tier,
            "score": score
        })

    # Add current tier if not already in history
    if history and history[0]["tier"] != current_tier:
        history.insert(0, {
            "date": datetime.utcnow().isoformat(),
            "tier": current_tier,
            "score": history[0]["score"] if history else 0.0
        })

    return {
        "server_id": server_id,
        "history": history
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session as original_get_session

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[original_get_session] = lambda: SessionLocal()

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Insert test data
    with SessionLocal() as session:
        server_id = "test-server-123"

        # Insert server registry
        session.add(McpServerRegistry(
            server_id=server_id,
            risk_tier="Medium",
            last_assessed=datetime.utcnow()
        ))

        # Insert historical scores
        now = datetime.utcnow()
        session.add(McpLlmAxisScore(
            server_id=server_id,
            axis_name="overall_risk",
            p_top=0.85,
            scored_at=now - timedelta(days=2)
        ))
        session.add(McpLlmAxisScore(
            server_id=server_id,
            axis_name="overall_risk",
            p_top=0.75,
            scored_at=now - timedelta(days=1)
        ))
        session.add(McpLlmAxisScore(
            server_id=server_id,
            axis_name="overall_risk",
            p_top=0.65,
            scored_at=now
        ))
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get(f"/api/server/{server_id}/risk_tier_history?days=3")

    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 3
    assert data["history"][2]["tier"] == "Medium"  # Earliest date should be Medium
    print("PASS")