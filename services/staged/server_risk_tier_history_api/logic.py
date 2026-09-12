from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()

class RiskTierHistoryItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    days: int
    history: List[RiskTierHistoryItem]

def get_risk_tier_history(server_id: str, days: int = 30, db: Session = Depends(get_session)) -> RiskTierHistoryResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get all scores for the server within the date range
    scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).order_by(McpLlmAxisScore.scored_at).all()

    if not scores:
        raise HTTPException(status_code=404, detail="No risk tier history found for the given server")

    # Get the server's current risk tier
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Group scores by date and count the number of scores for each date
    history = []
    current_date = None
    current_count = 0

    for score in scores:
        date = score.scored_at.date()
        if date != current_date:
            if current_date is not None:
                history.append({
                    "date": current_date.isoformat(),
                    "tier": server.risk_tier,
                    "count": current_count
                })
            current_date = date
            current_count = 1
        else:
            current_count += 1

    # Add the last group
    if current_date is not None:
        history.append({
            "date": current_date.isoformat(),
            "tier": server.risk_tier,
            "count": current_count
        })

    return RiskTierHistoryResponse(
        server_id=server_id,
        days=days,
        history=history
    )

@router.get("/servers/{server_id}/risk_tier_history", response_model=RiskTierHistoryResponse)
async def get_server_risk_tier_history(
    server_id: str,
    days: Optional[int] = 30,
    db: Session = Depends(get_session)
):
    return get_risk_tier_history(server_id, days, db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:", echo=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Create a test app with dependency overrides
    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Insert test data
    test_client = TestClient(test_app)

    with SessionLocal() as session:
        # Insert a server
        server = McpServerRegistry(
            server_id="test-server-1",
            name="Test Server 1",
            url="https://example.com",
            risk_tier="high",
            last_seen=datetime.utcnow(),
            scan_count=1,
            confidence=0.9,
            verdict="malicious",
            verdict_reasoning="Test reasoning"
        )
        session.add(server)

        # Insert some scores
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        score1 = McpLlmAxisScore(
            server_id="test-server-1",
            axis_name="risk",
            label="high",
            label_index=2,
            probs=[0.1, 0.2, 0.7],
            scored_at=now,
            model_version="1.0",
            decision_rule_version="1.0",
            adapter_sha256="test-sha-1"
        )

        score2 = McpLlmAxisScore(
            server_id="test-server-1",
            axis_name="risk",
            label="medium",
            label_index=1,
            probs=[0.1, 0.7, 0.2],
            scored_at=yesterday,
            model_version="1.0",
            decision_rule_version="1.0",
            adapter_sha256="test-sha-2"
        )

        score3 = McpLlmAxisScore(
            server_id="test-server-1",
            axis_name="risk",
            label="medium",
            label_index=1,
            probs=[0.1, 0.7, 0.2],
            scored_at=two_days_ago,
            model_version="1.0",
            decision_rule_version="1.0",
            adapter_sha256="test-sha-3"
        )

        session.add_all([score1, score2, score3])
        session.commit()

    # Test the endpoint
    response = test_client.get("/servers/test-server-1/risk_tier_history?days=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 3
    assert data["history"][0]["tier"] == "high"
    assert data["history"][1]["tier"] == "medium"
    assert data["history"][2]["tier"] == "medium"
    print("PASS")