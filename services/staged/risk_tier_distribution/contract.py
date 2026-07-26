from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/risk")

class TierDistribution(BaseModel):
    date: str
    tier_counts: Dict[str, int]

class RiskTierDistributionResponse(BaseModel):
    days: int
    distribution: List[TierDistribution]

def get_risk_tier_distribution(days: int, session: Session = Depends(get_session)) -> RiskTierDistributionResponse:
    cutoff_date = datetime.now() - timedelta(days=days)
    query = (
        session.query(
            func.date(McpLlmAxisScore.scored_at).label('day'),
            McpServerRegistry.risk_tier,
            func.count(McpLlmAxisScore.server_id.distinct()).label('count')
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff_date)
        .group_by('day', 'risk_tier')
        .order_by('day')
        .all()
    )

    distribution = []
    current_date = None
    current_tiers = {}

    for row in query:
        if row.day != current_date:
            if current_date is not None:
                distribution.append(TierDistribution(
                    date=current_date.strftime('%Y-%m-%d'),
                    tier_counts=current_tiers
                ))
            current_date = row.day
            current_tiers = {}
        current_tiers[row.risk_tier] = row.count

    if current_date is not None:
        distribution.append(TierDistribution(
            date=current_date.strftime('%Y-%m-%d'),
            tier_counts=current_tiers
        ))

    return RiskTierDistributionResponse(days=days, distribution=distribution)

router.get("/tier_distribution")(get_risk_tier_distribution)

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
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Add test data for two days
        from datetime import datetime, timedelta

        yesterday = datetime.now() - timedelta(days=1)
        day_before = datetime.now() - timedelta(days=2)

        # Server registry data
        session.add_all([
            McpServerRegistry(server_id=1, risk_tier="TRUSTED_GENERAL", last_seen=yesterday),
            McpServerRegistry(server_id=2, risk_tier="TRUSTED_RESEARCH", last_seen=yesterday),
            McpServerRegistry(server_id=3, risk_tier="TRUSTED_GENERAL", last_seen=day_before),
            McpServerRegistry(server_id=4, risk_tier="UNTRUSTED", last_seen=day_before),
            McpServerRegistry(server_id=5, risk_tier="UNTRUSTED", last_seen=day_before),
        ])

        # Axis scores data
        session.add_all([
            McpLlmAxisScore(server_id=1, axis_name="test_axis", p_top=0.9, scored_at=yesterday),
            McpLlmAxisScore(server_id=2, axis_name="test_axis", p_top=0.8, scored_at=yesterday),
            McpLlmAxisScore(server_id=3, axis_name="test_axis", p_top=0.7, scored_at=day_before),
            McpLlmAxisScore(server_id=4, axis_name="test_axis", p_top=0.6, scored_at=day_before),
            McpLlmAxisScore(server_id=5, axis_name="test_axis", p_top=0.5, scored_at=day_before),
        ])

        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier_distribution?days=2")

    assert response.status_code == 200
    data = response.json()

    assert data["days"] == 2
    assert len(data["distribution"]) == 2

    # Verify counts for each day
    day_counts = {item["date"]: item["tier_counts"] for item in data["distribution"]}

    # Expected counts based on test data
    expected_counts = {
        (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'): {
            "TRUSTED_GENERAL": 1,
            "TRUSTED_RESEARCH": 1
        },
        (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d'): {
            "TRUSTED_GENERAL": 1,
            "UNTRUSTED": 2
        }
    }

    for date, counts in day_counts.items():
        assert counts == expected_counts[date]

    print("PASS")