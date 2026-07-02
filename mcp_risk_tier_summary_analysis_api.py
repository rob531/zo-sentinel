# deps: fastapi, pydantic, sqlalchemy

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk-tier-summary"])

class RiskTierSummary(BaseModel):
    tier: str
    count: int
    percentage: float

@router.get("/risk-tier-summary", response_model=list[RiskTierSummary])
def get_risk_tier_summary(db: Session = Depends(get_session)) -> list[RiskTierSummary]:
    """Get the summary of risk tiers from the server registry."""
    total_servers = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    if total_servers == 0:
        return []

    tier_counts = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count().label("count")
        ).group_by(McpServerRegistry.risk_tier)
    ).all()

    summary = []
    for tier, count in tier_counts:
        percentage = (count / total_servers) * 100
        summary.append(RiskTierSummary(tier=tier, count=count, percentage=percentage))

    return summary

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
    s.add(McpServerRegistry(server_id="srv1", name="Server 1", url="http://example.com/1", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv2", name="Server 2", url="http://example.com/2", risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv3", name="Server 3", url="http://example.com/3", risk_tier="LOW"))
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
    r = c.get("/api/risk-tier-summary"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j) == 3, j
    assert j[0]["tier"] == "HIGH", j
    assert j[0]["count"] == 1, j
    assert j[0]["percentage"] == 33.33333333333333, j
    print("PASS")