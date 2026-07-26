from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

app = FastAPI()

class TierHistoryItem(BaseModel):
    date: str
    tier: str
    count: int

class TierHistoryResponse(BaseModel):
    server_id: str
    days: int
    history: List[TierHistoryItem]

def get_risk_tier(p_critical: float) -> str:
    if p_critical >= 0.9:
        return "high"
    elif p_critical >= 0.7:
        return "medium"
    elif p_critical >= 0.5:
        return "low"
    else:
        return "minimal"

def get_tier_history(server_id: str, days: int, session: Session) -> List[TierHistoryItem]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    query = session.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.p_top,
        McpServerRegistry.name
    ).join(
        McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == "p_critical",
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).order_by(
        McpLlmAxisScore.scored_at.desc()
    ).all()

    history = []
    date_counts = {}

    for scored_at, p_top, name in query:
        date_str = scored_at.strftime("%Y-%m-%d")
        tier = get_risk_tier(p_top)

        if date_str in date_counts:
            date_counts[date_str]["count"] += 1
        else:
            date_counts[date_str] = {"date": date_str, "tier": tier, "count": 1}

    for date_str, data in date_counts.items():
        history.append(TierHistoryItem(**data))

    history.sort(key=lambda x: x.date)
    return history

@app.get("/api/risk/tier_history", response_model=TierHistoryResponse)
async def get_risk_tier_history(
    server_id: str = Query(...),
    days: int = Query(..., ge=1, le=365),
    session: Session = Depends(get_session)
):
    history = get_tier_history(server_id, days, session)

    if not history:
        raise HTTPException(status_code=404, detail="No tier history found for the specified server and days")

    return TierHistoryResponse(
        server_id=server_id,
        days=days,
        history=history
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Insert test data
    test_session = TestSession()
    test_server_id = "test_server_1"
    test_server_name = "Test Server 1"

    test_server = McpServerRegistry(server_id=test_server_id, name=test_server_name)
    test_session.add(test_server)

    # Add scores for two days with varying p_critical values
    yesterday = datetime.now() - timedelta(days=1)
    day_before = datetime.now() - timedelta(days=2)

    test_scores = [
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="p_critical",
            p_top=0.95,
            scored_at=yesterday
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="p_critical",
            p_top=0.65,
            scored_at=day_before
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Create test client and make request
    client = TestClient(app)
    response = client.get(f"/api/risk/tier_history?server_id={test_server_id}&days=2")

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["tier"] == "high"
    assert data["history"][1]["tier"] == "low"

    print("PASS")