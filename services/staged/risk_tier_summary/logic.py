from typing import List, Dict, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

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
    tier: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session)
) -> RiskTierSummary:
    query = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
        McpLlmAxisScore.p_top
    ).join(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).group_by(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
        McpLlmAxisScore.p_top
    )

    if tier:
        query = query.filter(McpServerRegistry.risk_tier == tier)

    results = query.all()

    tier_counts = {}
    for server_id, tier, score in results:
        if tier in tier_counts:
            tier_counts[tier] += 1
        else:
            tier_counts[tier] = 1

    top_servers = []
    for server_id, tier, score in sorted(results, key=lambda x: x[2], reverse=True)[:limit]:
        top_servers.append({
            "server_id": server_id,
            "tier": tier,
            "score": score
        })

    return RiskTierSummary(
        total_servers=len(results),
        tier_counts=[{"tier": k, "count": v} for k, v in tier_counts.items()],
        top_servers=top_servers
    )

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
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    session.add_all([
        McpServerRegistry(server_id="server1", risk_tier="HIGH_RISK_ISOLATED"),
        McpServerRegistry(server_id="server2", risk_tier="TRUSTED_GENERAL"),
        McpServerRegistry(server_id="server3", risk_tier="CAUTION_LIMITED"),
        McpLlmAxisScore(server_id="server1", axis_name="axis1", p_top=0.9),
        McpLlmAxisScore(server_id="server2", axis_name="axis1", p_top=0.7),
        McpLlmAxisScore(server_id="server3", axis_name="axis1", p_top=0.5),
    ])
    session.commit()

    client = TestClient(app)
    response = client.get("/api/risk/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 3
    assert len(data["tier_counts"]) == 3
    tier_counts = {item["tier"]: item["count"] for item in data["tier_counts"]}
    assert tier_counts["HIGH_RISK_ISOLATED"] == 1
    assert tier_counts["TRUSTED_GENERAL"] == 1
    assert tier_counts["CAUTION_LIMITED"] == 1
    assert len(data["top_servers"]) == 3
    print("PASS")