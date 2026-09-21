from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")

class RiskTierHistoryItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    days: int
    history: List[RiskTierHistoryItem]

@router.get(
    "/servers/{server_id}/risk_tier_history",
    response_model=RiskTierHistoryResponse,
    response_model_exclude_none=True,
)
async def get_risk_tier_history(
    server_id: str,
    days: Optional[int] = 30,
    session: Session = Depends(get_session),
) -> RiskTierHistoryResponse:
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get all scores for the server within the date range
    scores = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= cutoff_date,
        )
        .order_by(McpLlmAxisScore.scored_at.asc())
        .all()
    )

    if not scores:
        raise HTTPException(status_code=404, detail="No risk tier history found")

    # Get the current risk tier from server registry
    server = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Group scores by date and count occurrences of each risk tier
    history = []
    current_date = None
    current_tier = None
    current_count = 0

    for score in scores:
        date = score.scored_at.date().isoformat()
        tier = score.label

        if date != current_date or tier != current_tier:
            if current_date is not None:
                history.append(
                    RiskTierHistoryItem(
                        date=current_date, tier=current_tier, count=current_count
                    )
                )
            current_date = date
            current_tier = tier
            current_count = 1
        else:
            current_count += 1

    # Add the last group
    if current_date is not None:
        history.append(
            RiskTierHistoryItem(
                date=current_date, tier=current_tier, count=current_count
            )
        )

    return RiskTierHistoryResponse(
        server_id=server_id, days=days, history=history
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create in-memory SQLite database for testing
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    from app.models import Base

    Base.metadata.create_all(bind=test_engine)

    # Create test app with dependency override
    test_app = FastAPI()
    test_app.include_router(router)

    async def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Insert test data
    with SessionLocal() as session:
        # Insert server
        server = McpServerRegistry(
            server_id="test-server-1",
            name="Test Server",
            url="https://test.example.com",
            risk_tier="high",
            last_seen=datetime.utcnow(),
        )
        session.add(server)

        # Insert scores with different risk tiers
        now = datetime.utcnow()
        session.add(
            McpLlmAxisScore(
                server_id="test-server-1",
                label="high",
                scored_at=now - timedelta(days=2),
            )
        )
        session.add(
            McpLlmAxisScore(
                server_id="test-server-1",
                label="medium",
                scored_at=now - timedelta(days=1),
            )
        )
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/api/servers/test-server-1/risk_tier_history?days=3")

    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["date"] == (now - timedelta(days=2)).date().isoformat()
    assert data["history"][0]["tier"] == "high"
    assert data["history"][1]["date"] == (now - timedelta(days=1)).date().isoformat()
    assert data["history"][1]["tier"] == "medium"

    print("PASS")