from fastapi import Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func

class RiskTierHistoryItem(BaseModel):
    date: str
    tier: str
    score: float

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    history: List[RiskTierHistoryItem]

def get_risk_tier_history(server_id: str, days: int = 30, db: Session = Depends(get_session)) -> RiskTierHistoryResponse:
    # Get the current risk tier from server registry
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Calculate the date range for the history
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get the historical risk scores
    history = db.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.p_top,
        McpServerRegistry.risk_tier
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= cutoff_date
    ).order_by(
        McpLlmAxisScore.scored_at.asc()
    ).all()

    # Format the history
    formatted_history = []
    for item in history:
        formatted_history.append({
            "date": item.scored_at.isoformat(),
            "tier": item.risk_tier,
            "score": item.p_top
        })

    return RiskTierHistoryResponse(
        server_id=server_id,
        history=formatted_history
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency
    app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    # Insert test data
    test_server_id = "test_server_123"
    test_dates = [
        datetime.utcnow() - timedelta(days=2),
        datetime.utcnow() - timedelta(days=1),
        datetime.utcnow()
    ]

    with TestingSessionLocal() as db:
        # Insert server registry
        db.add(McpServerRegistry(
            server_id=test_server_id,
            risk_tier="high",
            last_assessed=datetime.utcnow()
        ))

        # Insert historical scores
        for i, date in enumerate(test_dates):
            db.add(McpLlmAxisScore(
                server_id=test_server_id,
                axis_name="overall_risk",
                p_top=0.1 * (i + 1),
                scored_at=date
            ))

        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/api/server/{test_server_id}/risk_tier_history?days=3")

    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 3
    assert data["history"][0]["date"] == test_dates[0].isoformat()
    assert data["history"][0]["tier"] == "high"

    print("PASS")