from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Dict, Any
from pydantic import BaseModel

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class TierCoverage(BaseModel):
    tier: str
    count: int
    pct: float

class CoverageResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    coverage_pct: float
    by_tier: List[TierCoverage]

class StaleServer(BaseModel):
    server_id: str
    last_scored_at: datetime
    hours_ago: float

def get_coverage_stats(session: Session) -> CoverageResponse:
    # Total servers
    total = session.query(MCPServerRegistry).count()

    # Scored servers
    scored = session.query(MCPLLMAxisScores.server_id).distinct().count()

    # Unscored servers
    unscored = total - scored

    # Coverage percentage
    coverage_pct = (scored / total) * 100 if total > 0 else 0.0

    # By tier
    tiers = session.query(
        MCPServerRegistry.tier,
        MCPServerRegistry.tier.label('tier'),
        MCPServerRegistry.tier.count().label('count')
    ).group_by(MCPServerRegistry.tier).all()

    by_tier = []
    for tier, count in tiers:
        pct = (count / total) * 100 if total > 0 else 0.0
        by_tier.append(TierCoverage(tier=tier, count=count, pct=pct))

    return CoverageResponse(
        total_servers=total,
        scored_servers=scored,
        unscored_servers=unscored,
        coverage_pct=coverage_pct,
        by_tier=by_tier
    )

def get_stale_servers(session: Session) -> List[StaleServer]:
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    stale_servers = session.query(
        MCPLLMAxisScores.server_id,
        MCPLLMAxisScores.created_at
    ).filter(
        MCPLLMAxisScores.created_at < seven_days_ago
    ).distinct(MCPLLMAxisScores.server_id).all()

    result = []
    for server_id, last_scored_at in stale_servers:
        hours_ago = (datetime.utcnow() - last_scored_at).total_seconds() / 3600
        result.append(StaleServer(
            server_id=server_id,
            last_scored_at=last_scored_at,
            hours_ago=hours_ago
        ))

    return result

@router.get("/scoring/coverage", response_model=CoverageResponse)
async def scoring_coverage(session: Session = Depends(get_session)):
    return get_coverage_stats(session)

@router.get("/scoring/coverage/stale", response_model=List[StaleServer])
async def scoring_coverage_stale(session: Session = Depends(get_session)):
    return get_stale_servers(session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the session dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(id="1", tier="tier1"),
        MCPServerRegistry(id="2", tier="tier2"),
        MCPServerRegistry(id="3", tier="tier1"),
    ])
    test_session.add_all([
        MCPLLMAxisScores(server_id="1", created_at=datetime.utcnow()),
        MCPLLMAxisScores(server_id="2", created_at=datetime.utcnow() - timedelta(days=8)),
    ])
    test_session.commit()

    # Test the API
    client = TestClient(app)

    # Test coverage endpoint
    response = client.get("/scoring/coverage")
    assert response.status_code == 200
    assert response.json()["coverage_pct"] > 0

    # Test stale endpoint
    response = client.get("/scoring/coverage/stale")
    assert response.status_code == 200
    assert len(response.json()) > 0

    print("PASS")