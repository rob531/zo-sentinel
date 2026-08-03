from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func, desc
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, CadenceJobRun
from app.schemas import MCPServerRegistrySchema, MCPLLMAxisScoresSchema, CadenceJobRunsSchema

router = APIRouter(prefix="/api/scoring")

class RiskTierBreakdown(BaseModel):
    tier: str
    count: int

class NeverScoredBurndownResponse(BaseModel):
    total_servers: int
    never_scored_count: int
    burndown_pct: float
    snapshot_date: Optional[datetime]
    daily_delta: Optional[float]
    days_remaining_estimate: Optional[int]
    risk_tier_breakdown: List[RiskTierBreakdown]

def get_never_scored_burndown(session=Depends(get_session)) -> NeverScoredBurndownResponse:
    # Count total servers
    total_count = session.query(func.count(McpServerRegistry.id)).scalar()

    # Count servers with no axis scores (never scored)
    never_scored_subquery = (
        session.query(McpServerRegistry.id)
        .outerjoin(McpLlmAxisScore, McpServerRegistry.id == McpLlmAxisScore.server_id)
        .group_by(McpServerRegistry.id)
        .having(func.count(McpLlmAxisScore.id) == 0)
        .subquery()
    )
    never_scored_count = session.query(func.count(never_scored_subquery.c.id)).scalar()

    # Calculate burndown percentage
    burndown_pct = (1 - (never_scored_count / total_count)) * 100 if total_count > 0 else 0.0

    # Get latest snapshot date
    snapshot = (
        session.query(CadenceJobRun)
        .filter(CadenceJobRun.job == 'never_scored_snapshot')
        .order_by(desc(CadenceJobRun.finished_at))
        .first()
    )
    snapshot_date = snapshot.finished_at if snapshot else None

    # Calculate daily delta if we have at least two snapshots
    daily_delta = None
    if snapshot:
        prev_snapshot = (
            session.query(CadenceJobRun)
            .filter(
                CadenceJobRun.job == 'never_scored_snapshot',
                CadenceJobRun.finished_at < snapshot.finished_at
            )
            .order_by(desc(CadenceJobRun.finished_at))
            .first()
        )
        if prev_snapshot:
            days_between = (snapshot.finished_at - prev_snapshot.finished_at).days
            if days_between > 0:
                daily_delta = (never_scored_count - prev_snapshot.data['never_scored_count']) / days_between

    # Estimate days remaining
    days_remaining_estimate = None
    if daily_delta and daily_delta > 0:
        days_remaining_estimate = int(never_scored_count / daily_delta)

    # Get risk tier breakdown
    risk_tier_breakdown = (
        session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.id).label('count')
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    risk_tier_breakdown = [RiskTierBreakdown(tier=tier, count=count) for tier, count in risk_tier_breakdown]

    return NeverScoredBurndownResponse(
        total_servers=total_count,
        never_scored_count=never_scored_count,
        burndown_pct=burndown_pct,
        snapshot_date=snapshot_date,
        daily_delta=daily_delta,
        days_remaining_estimate=days_remaining_estimate,
        risk_tier_breakdown=risk_tier_breakdown
    )

@router.get("/never-scored/burndown", response_model=NeverScoredBurndownResponse)
async def never_scored_burndown():
    return get_never_scored_burndown()

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Create 5 scored servers
        for i in range(1, 6):
            server = McpServerRegistry(
                id=f"server-scored-{i}",
                hostname=f"scored-server-{i}.example.com",
                risk_tier="low"
            )
            session.add(server)
            for axis in ["security", "reliability", "performance"]:
                score = McpLlmAxisScore(
                    server_id=server.id,
                    axis=axis,
                    score=0.8,
                    timestamp=datetime.now()
                )
                session.add(score)

        # Create 5 unscored servers
        for i in range(1, 6):
            server = McpServerRegistry(
                id=f"server-unscored-{i}",
                hostname=f"unscored-server-{i}.example.com",
                risk_tier="unknown"
            )
            session.add(server)

        # Create a snapshot
        snapshot = CadenceJobRun(
            job="never_scored_snapshot",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            data={"never_scored_count": 5}
        )
        session.add(snapshot)

        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/scoring/never-scored/burndown")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 10
    assert data["never_scored_count"] == 5
    assert data["burndown_pct"] == 50.0
    print("PASS")