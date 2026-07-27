from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/risk/tier/daily")

class RiskTierSeriesItem(BaseModel):
    date: str
    tier: str
    count: int

class RiskTierDailyResponse(BaseModel):
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

@router.get("/", response_model=RiskTierDailyResponse)
async def get_risk_tier_daily(
    days: int = Query(7, description="Number of days to look back"),
    session: Session = Depends(get_session)
):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    query = session.query(
        func.date(McpLlmAxisScore.scored_at).label('date'),
        func.count(McpServerRegistry.server_id.distinct()).label('count'),
        func.case(
            (McpLlmAxisScore.overall_risk >= 75, "TRUSTED_GENERAL"),
            (McpLlmAxisScore.overall_risk >= 60, "TRUSTED_RESEARCH"),
            (McpLlmAxisScore.overall_risk >= 45, "ENTERPRISE_CONTROLLED"),
            (McpLlmAxisScore.overall_risk >= 30, "CAUTION_LIMITED"),
            (McpLlmAxisScore.overall_risk >= 15, "HIGH_RISK_ISOLATED"),
            else_="KNOWN_THREAT"
        ).label('tier')
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        McpLlmAxisScore.axis_name == 'overall_risk',
        func.date(McpLlmAxisScore.scored_at) >= start_date,
        func.date(McpLlmAxisScore.scored_at) <= end_date
    ).group_by(
        func.date(McpLlmAxisScore.scored_at),
        'tier'
    ).order_by(
        func.date(McpLlmAxisScore.scored_at).desc(),
        'tier'
    ).all()

    series = [
        RiskTierSeriesItem(
            date=item.date.isoformat(),
            tier=item.tier,
            count=item.count
        ) for item in query
    ]

    return RiskTierDailyResponse(days=days, series=series)

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
        server1 = McpServerRegistry(
            server_id="server1",
            hostname="server1.example.com",
            ip_address="192.168.1.1",
            org_id=1
        )
        session.add(server1)

        # Server 2
        server2 = McpServerRegistry(
            server_id="server2",
            hostname="server2.example.com",
            ip_address="192.168.1.2",
            org_id=1
        )
        session.add(server2)

        # Scores for server1
        session.add(McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 1, 12, 0, 0),
            overall_risk=80.0
        ))
        session.add(McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 2, 12, 0, 0),
            overall_risk=50.0
        ))

        # Scores for server2
        session.add(McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 1, 12, 0, 0),
            overall_risk=30.0
        ))
        session.add(McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            scored_at=datetime(2023, 1, 2, 12, 0, 0),
            overall_risk=20.0
        ))

        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier/daily?days=2")

    assert response.status_code == 200
    data = response.json()

    assert data["days"] == 2
    assert len(data["series"]) == 4  # 2 days * 2 servers with different tiers

    # Check a known count for a specific tier on a known date
    found = False
    for item in data["series"]:
        if item["date"] == "2023-01-01" and item["tier"] == "TRUSTED_GENERAL":
            assert item["count"] == 1
            found = True
            break

    assert found

    print("PASS")