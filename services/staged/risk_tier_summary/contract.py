from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter(prefix="/api")

class TierCount(BaseModel):
    tier: str
    count: int

class ServerSummary(BaseModel):
    server_id: str
    tier: str
    score: float

class RiskTierSummary(BaseModel):
    total_servers: int
    tier_counts: List[TierCount]
    top_servers: List[ServerSummary]

def get_risk_tier_summary(
    tier: Optional[str] = Query(None),
    limit: int = Query(100),
    db: Session = Depends(get_session)
) -> RiskTierSummary:
    query = db.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
        func.avg(McpLlmAxisScore.p_top).label('avg_score')
    ).join(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).group_by(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier
    )

    if tier:
        query = query.filter(McpServerRegistry.risk_tier == tier)

    results = query.all()

    tier_counts = []
    for result in results:
        found = False
        for tc in tier_counts:
            if tc['tier'] == result.risk_tier:
                tc['count'] += 1
                found = True
                break
        if not found:
            tier_counts.append({'tier': result.risk_tier, 'count': 1})

    top_servers = []
    for result in results[:limit]:
        top_servers.append({
            'server_id': result.server_id,
            'tier': result.risk_tier,
            'score': float(result.avg_score)
        })

    return RiskTierSummary(
        total_servers=len(results),
        tier_counts=tier_counts,
        top_servers=top_servers
    )

@router.get("/risk/summary", response_model=RiskTierSummary)
async def risk_summary(
    tier: Optional[str] = Query(None),
    limit: int = Query(100),
    db: Session = Depends(get_session)
):
    return get_risk_tier_summary(tier=tier, limit=limit, db=db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(server_id="server1", risk_tier="HIGH_RISK_ISOLATED"),
        McpServerRegistry(server_id="server2", risk_tier="TRUSTED_GENERAL"),
        McpServerRegistry(server_id="server3", risk_tier="CAUTION_LIMITED"),
        McpLlmAxisScore(server_id="server1", axis_name="axis1", p_top=0.9),
        McpLlmAxisScore(server_id="server1", axis_name="axis2", p_top=0.8),
        McpLlmAxisScore(server_id="server2", axis_name="axis1", p_top=0.7),
        McpLlmAxisScore(server_id="server2", axis_name="axis2", p_top=0.6),
        McpLlmAxisScore(server_id="server3", axis_name="axis1", p_top=0.5),
        McpLlmAxisScore(server_id="server3", axis_name="axis2", p_top=0.4),
    ])
    test_session.commit()

    # Create FastAPI app and test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/risk/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 3
    assert len(data["tier_counts"]) == 3
    tiers = {tc["tier"] for tc in data["tier_counts"]}
    assert tiers == {"HIGH_RISK_ISOLATED", "TRUSTED_GENERAL", "CAUTION_LIMITED"}
    assert len(data["top_servers"]) == 3

    print("PASS")