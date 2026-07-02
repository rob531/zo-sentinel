# deps: fastapi, pydantic, sqlalchemy

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk-tier-distribution"])

class RiskTierDistribution(BaseModel):
    tier: str
    count: int

@router.get("/risk-tiers/distribution", response_model=list[RiskTierDistribution])
def get_risk_tier_distribution(db: Session = Depends(get_session)) -> list[RiskTierDistribution]:
    """Get the distribution of risk tiers across all servers."""
    rows = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id)
        ).group_by(McpServerRegistry.risk_tier)
    ).all()
    return [RiskTierDistribution(tier=tier, count=count) for tier, count in rows]

if __name__ == "__main__":
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
    s.add(McpServerRegistry(server_id="srv1", name="Server 1", url="https://example.com/srv1", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv2", name="Server 2", url="https://example.com/srv2", risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv3", name="Server 3", url="https://example.com/srv3", risk_tier="LOW"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/risk-tiers/distribution"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j) == 3, j
    assert any(item["tier"] == "HIGH" and item["count"] == 1 for item in j), j
    assert any(item["tier"] == "MEDIUM" and item["count"] == 1 for item in j), j
    assert any(item["tier"] == "LOW" and item["count"] == 1 for item in j), j
    print("PASS")
