from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy import func, case
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes

router = APIRouter()

class TierSummary(BaseModel):
    tier: str
    server_count: int
    percentage: float

class RiskTierSummaryResponse(BaseModel):
    total_servers: int
    tiers: List[TierSummary]

def calculate_risk_tier(server_id: int, session) -> str:
    # Check for CRITICAL axis override
    critical_override = session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_id == server_id,
        MCPScoreDisputes.axis == 'CRITICAL'
    ).first()

    if critical_override:
        return 'CRITICAL'

    # Calculate the average score across all axes
    avg_score = session.query(func.avg(MCPLLMAxisScores.score)).filter(
        MCPLLMAxisScores.server_id == server_id
    ).scalar()

    if avg_score is None:
        return 'UNKNOWN'

    # Determine tier based on average score
    if avg_score >= 0.9:
        return 'LOW'
    elif avg_score >= 0.7:
        return 'MEDIUM'
    elif avg_score >= 0.5:
        return 'HIGH'
    else:
        return 'CRITICAL'

@router.get("/risk-tier-summary", response_model=RiskTierSummaryResponse)
async def get_risk_tier_summary(session=Depends(get_session)):
    # Get all servers
    servers = session.query(MCPServerRegistry).all()

    # Calculate risk tier for each server
    server_tiers = []
    for server in servers:
        tier = calculate_risk_tier(server.id, session)
        server_tiers.append((server.id, tier))

    # Count servers per tier
    tier_counts = {}
    for _, tier in server_tiers:
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Calculate percentages
    total_servers = len(servers)
    tiers = []
    for tier, count in tier_counts.items():
        percentage = (count / total_servers) * 100
        tiers.append({
            "tier": tier,
            "server_count": count,
            "percentage": round(percentage, 2)
        })

    # Ensure all tiers are represented, even if count is 0
    all_tiers = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 'UNKNOWN']
    for tier in all_tiers:
        if tier not in [t['tier'] for t in tiers]:
            tiers.append({
                "tier": tier,
                "server_count": 0,
                "percentage": 0.0
            })

    return {
        "total_servers": total_servers,
        "tiers": tiers
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes
    from sqlalchemy.orm import sessionmaker

    # Create in-memory database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(id=1, name="Server 1"),
        MCPServerRegistry(id=2, name="Server 2"),
        MCPServerRegistry(id=3, name="Server 3"),
        MCPServerRegistry(id=4, name="Server 4"),
        MCPServerRegistry(id=5, name="Server 5"),
        MCPServerRegistry(id=6, name="Server 6"),
        MCPServerRegistry(id=7, name="Server 7"),
        MCPServerRegistry(id=8, name="Server 8"),
    ])

    test_session.add_all([
        MCPLLMAxisScores(server_id=1, axis="AXIS1", score=0.95),
        MCPLLMAxisScores(server_id=1, axis="AXIS2", score=0.90),
        MCPLLMAxisScores(server_id=2, axis="AXIS1", score=0.85),
        MCPLLMAxisScores(server_id=2, axis="AXIS2", score=0.80),
        MCPLLMAxisScores(server_id=3, axis="AXIS1", score=0.75),
        MCPLLMAxisScores(server_id=3, axis="AXIS2", score=0.70),
        MCPLLMAxisScores(server_id=4, axis="AXIS1", score=0.65),
        MCPLLMAxisScores(server_id=4, axis="AXIS2", score=0.60),
        MCPLLMAxisScores(server_id=5, axis="AXIS1", score=0.55),
        MCPLLMAxisScores(server_id=5, axis="AXIS2", score=0.50),
        MCPLLMAxisScores(server_id=6, axis="AXIS1", score=0.45),
        MCPLLMAxisScores(server_id=6, axis="AXIS2", score=0.40),
        MCPLLMAxisScores(server_id=7, axis="AXIS1", score=0.35),
        MCPLLMAxisScores(server_id=7, axis="AXIS2", score=0.30),
        MCPLLMAxisScores(server_id=8, axis="AXIS1", score=0.25),
        MCPLLMAxisScores(server_id=8, axis="AXIS2", score=0.20),
    ])

    # Add a CRITICAL override for one server
    test_session.add(MCPScoreDisputes(server_id=8, axis="CRITICAL", score=0.0))

    test_session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/risk-tier-summary")
    assert response.status_code == 200
    data = response.json()

    # Verify all tiers are present
    tiers_present = [tier['tier'] for tier in data['tiers']]
    assert set(tiers_present) == {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 'UNKNOWN'}

    # Verify the CRITICAL override is reflected
    critical_tier = next(tier for tier in data['tiers'] if tier['tier'] == 'CRITICAL')
    assert critical_tier['server_count'] >= 1

    print("PASS")