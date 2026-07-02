# deps: fastapi, pydantic, sqlalchemy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from typing import Dict, Optional

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk-tier-trend"])

class RiskTierTrendResponse(BaseModel):
    risk_tier: str
    count: int
    trend: str

@router.get("/risk-tier-trend", response_model=Dict[str, RiskTierTrendResponse])
def risk_tier_trend(db: Session = Depends(get_session)) -> Dict[str, RiskTierTrendResponse]:
    """Get the count and trend of servers for each risk tier."""
    risk_tiers = ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN", "NONE"]
    trend_data = {}
    
    for tier in risk_tiers:
        count = db.execute(
            select(func.count()).where(McpServerRegistry.risk_tier == tier)
        ).scalar() or 0
        
        # Placeholder for trend calculation logic
        trend = "stable"  # This will be replaced with actual trend calculation
        
        trend_data[tier] = RiskTierTrendResponse(
            risk_tier=tier,
            count=count,
            trend=trend
        )
    
    return trend_data

if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    
    # Seed the database with test data
    s.add(McpServerRegistry(server_id="srv1", name="Server 1", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv2", name="Server 2", risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv3", name="Server 3", risk_tier="LOW"))
    s.commit(); s.close()
    
    app = FastAPI(); app.include_router(router)
    
    def override_get_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()
    
    app.dependency_overrides[get_session] = override_get_session
    c = TestClient(app)
    r = c.get("/api/risk-tier-trend"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j) == 6, j  # Ensure all 6 risk tiers are present
    assert j["HIGH"]["count"] == 1, j
    assert j["MEDIUM"]["count"] == 1, j
    assert j["LOW"]["count"] == 1, j
    assert j["CRITICAL"]["count"] == 0, j
    assert j["UNKNOWN"]["count"] == 0, j
    assert j["NONE"]["count"] == 0, j
    print("PASS")
