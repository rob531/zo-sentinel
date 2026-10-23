# services/staged/mcp_risk_tier_distribution_api/router.py
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["risk"])


class TierCount(BaseModel):
    tier: str
    count: int
    pct: float


class TierDistributionResponse(BaseModel):
    total: int
    distribution: List[TierCount]


def get_tier_distribution(
    session: Session,
    source: Optional[str] = None
) -> TierDistributionResponse:
    query = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label('count')
    )
    
    if source:
        query = query.filter(McpServerRegistry.registry_source == source)
    
    query = query.group_by(McpServerRegistry.risk_tier)
    
    results = query.all()
    
    total = sum(r.count for r in results)
    
    distribution = [
        TierCount(
            tier=r.risk_tier or "unknown",
            count=r.count,
            pct=round(r.count / total * 100, 2) if total > 0 else 0.0
        )
        for r in results
    ]
    
    return TierDistributionResponse(total=total, distribution=distribution)


@router.get("/risk/tier-distribution", response_model=TierDistributionResponse)
def api_get_tier_distribution(
    source: Optional[str] = Query(None, description="Filter by registry source"),
    session: Session = Depends(get_session)
) -> TierDistributionResponse:
    return get_tier_distribution(session=session, source=source)


if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    # In-memory test database
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    # Seed 5 servers across 3 tiers
    session = TestingSessionLocal()
    try:
        # Tier 1: 2 servers
        session.add(McpServerRegistry(server_id="s1", risk_tier="1", registry_source="prod"))
        session.add(McpServerRegistry(server_id="s2", risk_tier="1", registry_source="prod"))
        # Tier 2: 2 servers
        session.add(McpServerRegistry(server_id="s3", risk_tier="2", registry_source="staging"))
        session.add(McpServerRegistry(server_id="s4", risk_tier="2", registry_source="staging"))
        # Tier 3: 1 server
        session.add(McpServerRegistry(server_id="s5", risk_tier="3", registry_source="dev"))
        session.commit()
    finally:
        session.close()
    
    # Create test app
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get("/api/risk/tier-distribution")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    
    assert data["total"] == 5, f"Expected total=5, got {data['total']}"
    
    dist_sum = sum(d["count"] for d in data["distribution"])
    assert dist_sum == data["total"], f"Distribution sum {dist_sum} != total {data['total']}"
    
    assert len(data["distribution"]) == 3, f"Expected 3 tiers, got {len(data['distribution'])}"
    
    # Test source filter
    response_filtered = client.get("/api/risk/tier-distribution?source=prod")
    assert response_filtered.status_code == 200
    data_filtered = response_filtered.json()
    assert data_filtered["total"] == 2
    
    print("PASS")