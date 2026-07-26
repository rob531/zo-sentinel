from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/risk")

class RiskTierHistoryItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierHistoryResponse(BaseModel):
    server_id: str
    days: int
    history: List[RiskTierHistoryItem]

def get_risk_tier(score: float) -> str:
    if score >= 0.9:
        return "critical"
    elif score >= 0.7:
        return "high"
    elif score >= 0.5:
        return "medium"
    elif score >= 0.3:
        return "low"
    else:
        return "minimal"

@router.get("/tier_history", response_model=RiskTierHistoryResponse)
async def get_risk_tier_history(
    server_id: str = Query(...),
    days: int = Query(7),
    session: Session = Depends(get_session)
):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    query = (
        session.query(
            func.date(McpLlmAxisScore.scored_at).label('date'),
            func.max(McpLlmAxisScore.p_top).label('max_p_top'),
            func.count().label('count')
        )
        .join(McpServerRegistry, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .filter(
            and_(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.scored_at >= start_date,
                McpLlmAxisScore.scored_at <= end_date
            )
        )
        .group_by(func.date(McpLlmAxisScore.scored_at))
        .order_by(func.date(McpLlmAxisScore.scored_at).desc())
    )

    results = query.all()

    history = []
    for row in results:
        tier = get_risk_tier(row.max_p_top)
        history.append({
            "date": row.date.strftime("%Y-%m-%d"),
            "tier": tier,
            "count": row.count
        })

    return {
        "server_id": server_id,
        "days": days,
        "history": history
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    with SessionLocal() as session:
        # Add test server
        test_server = McpServerRegistry(server_id="test-server-1", name="Test Server 1")
        session.add(test_server)

        # Add test scores for two days
        yesterday = datetime.now() - timedelta(days=1)
        day_before = datetime.now() - timedelta(days=2)

        # Day before: critical score
        session.add(McpLlmAxisScore(
            server_id="test-server-1",
            axis_name="overall_risk",
            p_top=0.95,
            scored_at=day_before
        ))

        # Yesterday: high score
        session.add(McpLlmAxisScore(
            server_id="test-server-1",
            axis_name="overall_risk",
            p_top=0.75,
            scored_at=yesterday
        ))

        session.commit()

    # Create test client
    test_app = FastAPI()
    test_app.include_router(router)
    client = TestClient(test_app)

    # Test the endpoint
    response = client.get("/api/risk/tier_history?server_id=test-server-1&days=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["tier"] == "high"
    assert data["history"][1]["tier"] == "critical"

    print("PASS")