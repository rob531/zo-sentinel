from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api/risk", tags=["server_risk_tier_distribution"])

class TierDistribution(BaseModel):
    tier: str
    count: int

class RiskTierDistributionResponse(BaseModel):
    total: int
    tiers: List[TierDistribution]

@router.get("/tier_distribution", response_model=RiskTierDistributionResponse)
def get_risk_tier_distribution(session: Session = Depends(get_session)):
    results = session.query(
        McpServerRegistry.risk_tier,
        McpServerRegistry.risk_tier.label("tier"),
        McpServerRegistry.risk_tier.count().label("count")
    ).group_by(McpServerRegistry.risk_tier).all()

    total = sum(count for _, count in results)
    tiers = [{"tier": tier, "count": count} for tier, count in results]

    return {"total": total, "tiers": tiers}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as session:
        session.add_all([
            McpServerRegistry(risk_tier="TRUSTED_GENERAL"),
            McpServerRegistry(risk_tier="ENTERPRISE_CONTROLLED"),
            McpServerRegistry(risk_tier="CAUTION_LIMITED")
        ])
        session.commit()

    client = TestClient(app)
    response = client.get("/api/risk/tier_distribution")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["tiers"]) == 3
    tier_counts = {tier["tier"]: tier["count"] for tier in data["tiers"]}
    assert tier_counts["TRUSTED_GENERAL"] == 1
    assert tier_counts["ENTERPRISE_CONTROLLED"] == 1
    assert tier_counts["CAUTION_LIMITED"] == 1

    print("PASS")