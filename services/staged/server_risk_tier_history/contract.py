from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter(prefix="/api")

class RiskTierHistoryItem(BaseModel):
    date: str
    tier: str
    score: float

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    history: List[RiskTierHistoryItem]

def get_risk_tier(score: float) -> str:
    if score >= 0.8:
        return "high"
    elif score >= 0.5:
        return "medium"
    else:
        return "low"

@router.get("/server/{server_id}/risk_tier_history", response_model=RiskTierHistoryResponse)
async def get_server_risk_tier_history(
    server_id: str,
    days: Optional[int] = Query(30, ge=1, le=365),
    db: Session = Depends(get_session)
):
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get the risk tier history from the scores table
    scores = db.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.p_top
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= cutoff_date
    ).order_by(McpLlmAxisScore.scored_at.desc()).all()

    if not scores:
        raise HTTPException(status_code=404, detail="No risk tier history found for the given server")

    # Get the current risk tier from the registry
    current_tier = db.query(McpServerRegistry.risk_tier).filter(McpServerRegistry.server_id == server_id).scalar()

    history = []
    for scored_at, score in scores:
        tier = get_risk_tier(score)
        history.append({
            "date": scored_at.isoformat(),
            "tier": tier,
            "score": score
        })

    # Add the current tier if it's different from the last scored tier
    if current_tier and (not history or history[0]["tier"] != current_tier):
        last_assessed = db.query(McpServerRegistry.last_assessed).filter(McpServerRegistry.server_id == server_id).scalar()
        if last_assessed and last_assessed >= cutoff_date:
            history.insert(0, {
                "date": last_assessed.isoformat(),
                "tier": current_tier,
                "score": 0.0  # Placeholder since we don't have the exact score
            })

    return {"server_id": server_id, "history": history}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base

    # Override the session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # Insert test data
    with SessionLocal() as db:
        test_server_id = "test_server_123"

        # Insert into McpServerRegistry
        db.add(McpServerRegistry(
            server_id=test_server_id,
            risk_tier="medium",
            last_assessed=datetime.utcnow()
        ))

        # Insert into McpLlmAxisScore
        test_dates = [
            datetime.utcnow() - timedelta(days=2),
            datetime.utcnow() - timedelta(days=1),
            datetime.utcnow()
        ]
        test_scores = [0.4, 0.6, 0.7]

        for date, score in zip(test_dates, test_scores):
            db.add(McpLlmAxisScore(
                server_id=test_server_id,
                axis_name="overall_risk",
                p_top=score,
                scored_at=date
            ))

        db.commit()

    # Create a test client
    client = TestClient(app)

    # Test the endpoint
    response = client.get(f"/api/server/{test_server_id}/risk_tier_history?days=3")

    assert response.status_code == 200
    assert len(response.json()["history"]) == 3
    assert response.json()["history"][2]["date"] == test_dates[0].isoformat()
    assert response.json()["history"][2]["tier"] == "low"

    print("PASS")