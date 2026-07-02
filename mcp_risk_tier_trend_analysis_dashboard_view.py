from fastapi import APIRouter, Depends
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Dict, List
from pydantic import BaseModel

from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class RiskTierTrendResponse(BaseModel):
    date: str
    tier_counts: Dict[str, int]
    source_breakdown: Dict[str, Dict[str, int]]

@router.get("/risk-tiers/trend", response_model=List[RiskTierTrendResponse])
async def get_risk_tier_trend(db: Session = Depends(get_session)):
    thirty_days_ago = datetime.now() - timedelta(days=30)

    query = db.query(
        func.date(MCPServerRegistry.last_evaluated_at).label('date'),
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.registry_source
    ).filter(
        MCPServerRegistry.last_evaluated_at >= thirty_days_ago
    ).group_by(
        func.date(MCPServerRegistry.last_evaluated_at),
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.registry_source
    ).all()

    result = {}
    for row in query:
        date = row.date.isoformat()
        if date not in result:
            result[date] = {
                'tier_counts': {},
                'source_breakdown': {}
            }

        tier = row.risk_tier
        source = row.registry_source

        result[date]['tier_counts'][tier] = result[date]['tier_counts'].get(tier, 0) + 1

        if source not in result[date]['source_breakdown']:
            result[date]['source_breakdown'][source] = {}
        result[date]['source_breakdown'][source][tier] = result[date]['source_breakdown'][source].get(tier, 0) + 1

    return [
        RiskTierTrendResponse(
            date=date,
            tier_counts=entry['tier_counts'],
            source_breakdown=entry['source_breakdown']
        )
        for date, entry in result.items()
    ]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    test_session = TestSession()
    test_data = [
        MCPServerRegistry(
            last_evaluated_at=datetime.now() - timedelta(days=i),
            risk_tier="high",
            registry_source="source1"
        ) for i in range(30)
    ] + [
        MCPServerRegistry(
            last_evaluated_at=datetime.now() - timedelta(days=i),
            risk_tier="medium",
            registry_source="source2"
        ) for i in range(30)
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/risk-tiers/trend")
    assert response.status_code == 200
    print("PASS")