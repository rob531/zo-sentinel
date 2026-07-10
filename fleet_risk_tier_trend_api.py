from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, and_, or_, extract
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores

router = APIRouter()

class TierCounts(BaseModel):
    TRUSTED_GENERAL: int = 0
    ENTERPRISE_CONTROLLED: int = 0
    RESTRICTED: int = 0
    BLOCKED: int = 0

class Bucket(BaseModel):
    date: str
    tiers: TierCounts

class RiskTierTrendResponse(BaseModel):
    buckets: List[Bucket]
    criteria_version: str

def get_time_buckets(time_unit: str, start_date: datetime, end_date: datetime) -> List[str]:
    if time_unit == 'daily':
        delta = timedelta(days=1)
    elif time_unit == 'weekly':
        delta = timedelta(weeks=1)
    else:
        raise ValueError("Invalid time unit. Use 'daily' or 'weekly'.")

    buckets = []
    current_date = start_date
    while current_date <= end_date:
        buckets.append(current_date.strftime('%Y-%m-%d'))
        current_date += delta
    return buckets

def get_risk_tier_counts(session: Session, time_unit: str, start_date: datetime, end_date: datetime) -> List[Bucket]:
    time_buckets = get_time_buckets(time_unit, start_date, end_date)

    query = session.query(
        func.date_trunc(time_unit, MCPAxisScores.scored_at).label('bucket'),
        MCPAxisScores.risk_tier,
        func.count(MCPAxisScores.id).label('count')
    ).join(
        MCPServerRegistry, MCPServerRegistry.id == MCPAxisScores.server_id
    ).filter(
        MCPAxisScores.scored_at >= start_date,
        MCPAxisScores.scored_at <= end_date
    ).group_by(
        func.date_trunc(time_unit, MCPAxisScores.scored_at),
        MCPAxisScores.risk_tier
    ).all()

    result = []
    for bucket in time_buckets:
        tiers = TierCounts()
        for row in query:
            if row.bucket.strftime('%Y-%m-%d') == bucket:
                if row.risk_tier == 'TRUSTED_GENERAL':
                    tiers.TRUSTED_GENERAL = row.count
                elif row.risk_tier == 'ENTERPRISE_CONTROLLED':
                    tiers.ENTERPRISE_CONTROLLED = row.count
                elif row.risk_tier == 'RESTRICTED':
                    tiers.RESTRICTED = row.count
                elif row.risk_tier == 'BLOCKED':
                    tiers.BLOCKED = row.count
        result.append(Bucket(date=bucket, tiers=tiers))

    return result

@router.get("/fleet/risk-tier-trend", response_model=RiskTierTrendResponse)
async def get_fleet_risk_tier_trend(
    time_unit: str = 'daily',
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_session)
):
    if time_unit not in ['daily', 'weekly']:
        raise HTTPException(status_code=400, detail="Invalid time unit. Use 'daily' or 'weekly'.")

    if start_date is None:
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')

    start_date = datetime.strptime(start_date, '%Y-%m-%d')
    end_date = datetime.strptime(end_date, '%Y-%m-%d')

    buckets = get_risk_tier_counts(session, time_unit, start_date, end_date)

    criteria_version = session.query(MCPAxisScores.criteria_version).distinct().first()
    criteria_version = criteria_version[0] if criteria_version else "unknown"

    return RiskTierTrendResponse(buckets=buckets, criteria_version=criteria_version)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the get_session dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed the test database
    test_session = TestSession()
    test_server = MCPServerRegistry(id=1, hostname="test-server")
    test_session.add(test_server)
    test_session.commit()

    test_scores = [
        MCPAxisScores(
            server_id=1,
            scored_at=datetime(2023, 1, 1),
            risk_tier="TRUSTED_GENERAL",
            criteria_version="v1"
        ),
        MCPAxisScores(
            server_id=1,
            scored_at=datetime(2023, 1, 2),
            risk_tier="ENTERPRISE_CONTROLLED",
            criteria_version="v1"
        ),
        MCPAxisScores(
            server_id=1,
            scored_at=datetime(2023, 1, 3),
            risk_tier="RESTRICTED",
            criteria_version="v1"
        ),
        MCPAxisScores(
            server_id=1,
            scored_at=datetime(2023, 1, 4),
            risk_tier="BLOCKED",
            criteria_version="v1"
        ),
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Test the API
    client = TestClient(app)
    response = client.get("/fleet/risk-tier-trend")
    assert response.status_code == 200
    data = response.json()
    assert len(data["buckets"]) > 0
    assert data["criteria_version"] == "v1"
    print("PASS")