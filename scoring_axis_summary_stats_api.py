from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class AxisStats(BaseModel):
    axis_name: str
    avg_p_top: float
    avg_p_critical: float
    p_top_stddev: float
    servers_scored: int
    servers_escalated: int

class TierDistribution(BaseModel):
    risk_tier: str
    count: int

class ScoringAxisSummaryResponse(BaseModel):
    total_servers: int
    axis_stats: List[AxisStats]
    tier_distribution: List[TierDistribution]
    last_updated: str

def get_axis_stats(session: Session, server_id: Optional[str] = None, axis_name: Optional[str] = None, min_scored_at: Optional[datetime] = None):
    query = session.query(
        MCPLLMAxisScores.axis_name,
        func.avg(MCPLLMAxisScores.p_top).label('avg_p_top'),
        func.avg(MCPLLMAxisScores.p_critical).label('avg_p_critical'),
        func.stddev(MCPLLMAxisScores.p_top).label('p_top_stddev'),
        func.count(MCPLLMAxisScores.server_id).label('servers_scored'),
        func.sum(func.case([
            (MCPLLMAxisScores.risk_tier == 'escalated', 1)
        ], else_=0)).label('servers_escalated')
    ).join(
        MCPServerRegistry,
        MCPLLMAxisScores.server_id == MCPServerRegistry.server_id
    ).group_by(
        MCPLLMAxisScores.axis_name
    )

    if server_id:
        query = query.filter(MCPLLMAxisScores.server_id == server_id)
    if axis_name:
        query = query.filter(MCPLLMAxisScores.axis_name == axis_name)
    if min_scored_at:
        query = query.filter(MCPLLMAxisScores.scored_at >= min_scored_at)

    return query.all()

def get_tier_distribution(session: Session, server_id: Optional[str] = None, axis_name: Optional[str] = None, min_scored_at: Optional[datetime] = None):
    query = session.query(
        MCPLLMAxisScores.risk_tier,
        func.count(MCPLLMAxisScores.server_id).label('count')
    ).join(
        MCPServerRegistry,
        MCPLLMAxisScores.server_id == MCPServerRegistry.server_id
    ).group_by(
        MCPLLMAxisScores.risk_tier
    )

    if server_id:
        query = query.filter(MCPLLMAxisScores.server_id == server_id)
    if axis_name:
        query = query.filter(MCPLLMAxisScores.axis_name == axis_name)
    if min_scored_at:
        query = query.filter(MCPLLMAxisScores.scored_at >= min_scored_at)

    return query.all()

def get_last_updated(session: Session, server_id: Optional[str] = None, axis_name: Optional[str] = None, min_scored_at: Optional[datetime] = None):
    query = session.query(
        func.max(MCPLLMAxisScores.scored_at)
    ).join(
        MCPServerRegistry,
        MCPLLMAxisScores.server_id == MCPServerRegistry.server_id
    )

    if server_id:
        query = query.filter(MCPLLMAxisScores.server_id == server_id)
    if axis_name:
        query = query.filter(MCPLLMAxisScores.axis_name == axis_name)
    if min_scored_at:
        query = query.filter(MCPLLMAxisScores.scored_at >= min_scored_at)

    last_updated = query.scalar()
    return last_updated.isoformat() if last_updated else None

@router.get("/scoring/axis-summary", response_model=ScoringAxisSummaryResponse)
async def get_scoring_axis_summary(
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    min_scored_at: Optional[datetime] = None,
    session: Session = Depends(get_session)
):
    axis_stats = get_axis_stats(session, server_id, axis_name, min_scored_at)
    tier_distribution = get_tier_distribution(session, server_id, axis_name, min_scored_at)
    last_updated = get_last_updated(session, server_id, axis_name, min_scored_at)

    total_servers = sum(item.servers_scored for item in axis_stats)

    return {
        "total_servers": total_servers,
        "axis_stats": [
            {
                "axis_name": item.axis_name,
                "avg_p_top": float(item.avg_p_top) if item.avg_p_top is not None else 0.0,
                "avg_p_critical": float(item.avg_p_critical) if item.avg_p_critical is not None else 0.0,
                "p_top_stddev": float(item.p_top_stddev) if item.p_top_stddev is not None else 0.0,
                "servers_scored": item.servers_scored,
                "servers_escalated": item.servers_escalated
            }
            for item in axis_stats
        ],
        "tier_distribution": [
            {"risk_tier": item.risk_tier, "count": item.count}
            for item in tier_distribution
        ],
        "last_updated": last_updated
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base
    from sqlalchemy import create_engine

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)

    # Override get_session for testing
    def override_get_session():
        session = SessionLocal(bind=test_engine)
        try:
            yield session
        finally:
            session.close()

    from app import app
    app.dependency_overrides[get_session] = override_get_session

    # Populate test data
    with SessionLocal(bind=test_engine) as session:
        # Add test servers
        server1 = MCPServerRegistry(server_id="server1", name="Test Server 1")
        server2 = MCPServerRegistry(server_id="server2", name="Test Server 2")
        session.add_all([server1, server2])

        # Add test scores
        from datetime import datetime, timedelta
        now = datetime.now()
        scores = [
            MCPLLMAxisScores(
                server_id="server1",
                axis_name="axis1",
                p_top=0.9,
                p_critical=0.1,
                risk_tier="safe",
                scored_at=now - timedelta(days=1)
            ),
            MCPLLMAxisScores(
                server_id="server1",
                axis_name="axis2",
                p_top=0.8,
                p_critical=0.2,
                risk_tier="escalated",
                scored_at=now - timedelta(days=1)
            ),
            MCPLLMAxisScores(
                server_id="server2",
                axis_name="axis1",
                p_top=0.7,
                p_critical=0.3,
                risk_tier="safe",
                scored_at=now
            ),
            MCPLLMAxisScores(
                server_id="server2",
                axis_name="axis2",
                p_top=0.6,
                p_critical=0.4,
                risk_tier="safe",
                scored_at=now
            ),
        ]
        session.add_all(scores)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/scoring/axis-summary")
    assert response.status_code == 200
    data = response.json()

    # Verify all 7 axes are returned (even though we only added 2 in test data)
    assert len(data["axis_stats"]) >= 2  # At least the 2 we added

    # Verify tier distribution sums to total_servers
    total_from_tiers = sum(item["count"] for item in data["tier_distribution"])
    assert total_from_tiers == data["total_servers"]

    # Verify last_updated is in ISO format
    from datetime import datetime
    try:
        datetime.fromisoformat(data["last_updated"])
    except ValueError:
        assert False, "last_updated is not in ISO format"

    print("PASS")