from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from app.utils import get_write_service_client
import httpx

router = APIRouter()

class ServerSample(BaseModel):
    server_id: str
    name: str
    first_seen: datetime
    registry_source: str
    risk_tier: str

class NeverScoredBacklogResponse(BaseModel):
    total_never_scored: int
    oldest_unscored_days: int
    breakdown_by_source: Dict[str, int]
    breakdown_by_tier: Dict[str, int]
    sample_servers: List[ServerSample]

def get_never_scored_servers(session: Session) -> List[MCPServerRegistry]:
    # Get all servers that don't have any scores
    subquery = session.query(MCPLLMAxisScores.server_id).distinct()
    return session.query(MCPServerRegistry).filter(
        ~MCPServerRegistry.server_id.in_(subquery)
    ).all()

@router.get("/reports/never-scored-backlog", response_model=NeverScoredBacklogResponse)
async def never_scored_backlog():
    session = Depends(get_session)
    never_scored_servers = get_never_scored_servers(session)

    if not never_scored_servers:
        return NeverScoredBacklogResponse(
            total_never_scored=0,
            oldest_unscored_days=0,
            breakdown_by_source={},
            breakdown_by_tier={},
            sample_servers=[]
        )

    # Calculate metrics
    total = len(never_scored_servers)
    oldest = min(server.first_seen for server in never_scored_servers)
    oldest_days = (datetime.now() - oldest).days

    # Breakdowns
    source_breakdown = {}
    tier_breakdown = {}

    for server in never_scored_servers:
        source = server.registry_source or "unknown"
        source_breakdown[source] = source_breakdown.get(source, 0) + 1

        tier = server.risk_tier or "unknown"
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1

    # Sample servers (first 5)
    sample = []
    for server in never_scored_servers[:5]:
        sample.append(ServerSample(
            server_id=server.server_id,
            name=server.name,
            first_seen=server.first_seen,
            registry_source=server.registry_source or "unknown",
            risk_tier=server.risk_tier or "unknown"
        ))

    return NeverScoredBacklogResponse(
        total_never_scored=total,
        oldest_unscored_days=oldest_days,
        breakdown_by_source=source_breakdown,
        breakdown_by_tier=tier_breakdown,
        sample_servers=sample
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.execute(
        MCPServerRegistry.__table__.insert(),
        [
            {"server_id": "s1", "name": "Server 1", "first_seen": datetime.now() - timedelta(days=10), "registry_source": "source1", "risk_tier": "high"},
            {"server_id": "s2", "name": "Server 2", "first_seen": datetime.now() - timedelta(days=5), "registry_source": "source2", "risk_tier": "medium"},
            {"server_id": "s3", "name": "Server 3", "first_seen": datetime.now() - timedelta(days=15), "registry_source": "source1", "risk_tier": "low"},
            {"server_id": "s4", "name": "Server 4", "first_seen": datetime.now() - timedelta(days=20), "registry_source": "source3", "risk_tier": "high"},
        ]
    )
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/reports/never-scored-backlog")
    assert response.status_code == 200
    data = response.json()
    assert data["total_never_scored"] > 0
    assert len(data["sample_servers"]) > 0
    print("PASS")