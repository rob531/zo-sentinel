from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

class TierHistoryItem(BaseModel):
    date: str
    tier: str
    count: int

class TierHistoryResponse(BaseModel):
    server_id: str
    days: int
    history: List[TierHistoryItem]

def get_risk_tier_history(
    server_id: str,
    days: int,
    session: Session = Depends(get_session)
) -> TierHistoryResponse:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Subquery to get the most critical axis for each day
    subquery = (
        select(
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top
        )
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= start_date,
            McpLlmAxisScore.scored_at <= end_date
        )
        .order_by(McpLlmAxisScore.scored_at, McpLlmAxisScore.p_top.desc())
        .cte("daily_critical_axis")
    )

    # Main query to join with server registry and aggregate by day
    query = (
        select(
            func.date(subquery.c.scored_at).label("date"),
            subquery.c.axis_name.label("tier"),
            func.count().label("count")
        )
        .select_from(
            subquery
            .join(
                McpServerRegistry,
                McpServerRegistry.server_id == server_id
            )
        )
        .group_by(func.date(subquery.c.scored_at), subquery.c.axis_name)
        .order_by(func.date(subquery.c.scored_at).desc())
    )

    result = session.execute(query)
    history = [
        TierHistoryItem(
            date=row.date.strftime("%Y-%m-%d"),
            tier=row.tier,
            count=row.count
        )
        for row in result
    ]

    return TierHistoryResponse(
        server_id=server_id,
        days=days,
        history=history
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Create tables
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)

    # Insert test data
    test_server_id = "test_server_1"
    test_server_name = "Test Server"

    # Insert server registry
    test_session.add(McpServerRegistry(
        server_id=test_server_id,
        name=test_server_name
    ))

    # Insert axis scores for two days
    yesterday = datetime.utcnow() - timedelta(days=1)
    day_before = datetime.utcnow() - timedelta(days=2)

    test_session.add_all([
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="low_risk",
            p_top=0.1,
            scored_at=yesterday
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="medium_risk",
            p_top=0.5,
            scored_at=yesterday
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="high_risk",
            p_top=0.9,
            scored_at=yesterday
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="low_risk",
            p_top=0.2,
            scored_at=day_before
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="medium_risk",
            p_top=0.6,
            scored_at=day_before
        )
    ])

    test_session.commit()

    # Override dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: test_session

    # Include the router
    from services.staged.risk_tier_history import router
    app.include_router(router.router)

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/api/risk/tier_history?server_id={test_server_id}&days=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 2
    assert any(item["tier"] == "high_risk" for item in data["history"])
    assert any(item["tier"] == "medium_risk" for item in data["history"])

    print("PASS")