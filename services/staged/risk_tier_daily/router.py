from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/risk/tier/daily")

class RiskTierSeriesItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierResponse(BaseModel):
    days: int
    series: List[RiskTierSeriesItem]

def get_risk_tier(score: float) -> str:
    if score >= 75:
        return "TRUSTED_GENERAL"
    elif score >= 60:
        return "TRUSTED_RESEARCH"
    elif score >= 45:
        return "ENTERPRISE_CONTROLLED"
    elif score >= 30:
        return "CAUTION_LIMITED"
    elif score >= 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

@router.get("/", response_model=RiskTierResponse)
async def get_daily_risk_tiers(
    days: int = Query(7, description="Number of days to look back"),
    session: Session = Depends(get_session)
):
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    query = session.query(
        McpLlmAxisScore.scored_at,
        McpServerRegistry.server_id,
        McpLlmAxisScore.overall_risk
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    )

    results = query.all()

    series = []
    for day in range(days):
        current_date = end_date - timedelta(days=day)
        date_str = current_date.strftime("%Y-%m-%d")

        tier_counts = {}
        for result in results:
            if result.scored_at.date() == current_date.date():
                tier = get_risk_tier(result.overall_risk)
                tier_counts[tier] = tier_counts.get(tier, 0) + 1

        for tier, count in tier_counts.items():
            series.append({
                "date": date_str,
                "tier": tier,
                "count": count
            })

    return {"days": days, "series": series}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        # Server 1
        server1 = McpServerRegistry(server_id="server1", hostname="test1.example.com")
        session.add(server1)

        # Server 2
        server2 = McpServerRegistry(server_id="server2", hostname="test2.example.com")
        session.add(server2)

        # Scores for server1
        session.add(McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            overall_risk=80,
            scored_at=datetime.utcnow() - timedelta(days=1)
        ))
        session.add(McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            overall_risk=50,
            scored_at=datetime.utcnow()
        ))

        # Scores for server2
        session.add(McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            overall_risk=20,
            scored_at=datetime.utcnow() - timedelta(days=1)
        ))
        session.add(McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            overall_risk=10,
            scored_at=datetime.utcnow()
        ))

        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier/daily?days=2")

    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 2
    assert len(data["series"]) == 4  # 2 days * 2 servers

    # Check specific counts
    found = False
    for item in data["series"]:
        if item["date"] == datetime.utcnow().strftime("%Y-%m-%d") and item["tier"] == "ENTERPRISE_CONTROLLED":
            assert item["count"] == 1
            found = True
            break

    if found:
        print("PASS")
    else:
        print("FAIL")