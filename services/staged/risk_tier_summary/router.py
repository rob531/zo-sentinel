from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func, join

router = APIRouter(prefix="/api/risk")

class TierCount(BaseModel):
    tier: str
    count: int

class TopServer(BaseModel):
    server_id: str
    tier: str
    score: float

class RiskTierSummary(BaseModel):
    total_servers: int
    tier_counts: List[TierCount]
    top_servers: List[TopServer]

def get_risk_tier_summary(
    session: Session = Depends(get_session),
    tier: Optional[str] = Query(None),
    limit: int = Query(100)
) -> RiskTierSummary:
    # Join the tables and group by risk tier
    query = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label('count'),
        McpLlmAxisScore.p_top.label('score'),
        McpServerRegistry.server_id
    ).join(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).group_by(
        McpServerRegistry.risk_tier,
        McpLlmAxisScore.p_top,
        McpServerRegistry.server_id
    )

    if tier:
        query = query.filter(McpServerRegistry.risk_tier == tier)

    results = query.all()

    # Calculate total servers
    total_servers = len(results)

    # Get tier counts
    tier_counts = []
    for result in results:
        tier_counts.append({
            "tier": result.risk_tier,
            "count": result.count
        })

    # Remove duplicates and sort by score
    unique_servers = {}
    for result in results:
        if result.server_id not in unique_servers or result.score > unique_servers[result.server_id]['score']:
            unique_servers[result.server_id] = {
                "server_id": result.server_id,
                "tier": result.risk_tier,
                "score": result.score
            }

    top_servers = sorted(unique_servers.values(), key=lambda x: x['score'], reverse=True)[:limit]

    return RiskTierSummary(
        total_servers=total_servers,
        tier_counts=tier_counts,
        top_servers=top_servers
    )

@router.get("/summary", response_model=RiskTierSummary)
async def risk_summary(
    tier: Optional[str] = Query(None),
    limit: int = Query(100),
    session: Session = Depends(get_session)
):
    return get_risk_tier_summary(session, tier, limit)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        # Create test servers
        server1 = McpServerRegistry(
            server_id="server1",
            risk_tier="HIGH_RISK_ISOLATED"
        )
        server2 = McpServerRegistry(
            server_id="server2",
            risk_tier="TRUSTED_GENERAL"
        )
        server3 = McpServerRegistry(
            server_id="server3",
            risk_tier="CAUTION_LIMITED"
        )
        session.add_all([server1, server2, server3])

        # Create test scores
        score1 = McpLlmAxisScore(
            server_id="server1",
            axis_name="test_axis",
            p_top=0.9
        )
        score2 = McpLlmAxisScore(
            server_id="server2",
            axis_name="test_axis",
            p_top=0.7
        )
        score3 = McpLlmAxisScore(
            server_id="server3",
            axis_name="test_axis",
            p_top=0.5
        )
        session.add_all([score1, score2, score3])
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 3
    assert len(data["tier_counts"]) == 3
    assert any(tier["tier"] == "HIGH_RISK_ISOLATED" and tier["count"] == 1 for tier in data["tier_counts"])
    assert any(tier["tier"] == "TRUSTED_GENERAL" and tier["count"] == 1 for tier in data["tier_counts"])
    assert any(tier["tier"] == "CAUTION_LIMITED" and tier["count"] == 1 for tier in data["tier_counts"])
    print("PASS")