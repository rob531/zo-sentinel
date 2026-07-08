from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class RiskTierDistribution(BaseModel):
    tier: str
    count: int
    percentage: float
    pct_of_total: float

class RiskTierDistributionResponse(BaseModel):
    tiers: List[RiskTierDistribution]
    total: int
    registry_source_filter: Optional[str]

@router.get("/mcp/risk-tier/distribution", response_model=RiskTierDistributionResponse)
async def get_risk_tier_distribution(
    registry_source: Optional[str] = None,
    db: Session = Depends(get_session)
):
    query = db.query(
        MCPServerRegistry.risk_tier,
        func.count(MCPServerRegistry.id).label('count')
    ).group_by(MCPServerRegistry.risk_tier)

    if registry_source:
        query = query.filter(MCPServerRegistry.registry_source == registry_source)

    results = query.all()

    total = sum([r.count for r in results])

    tiers = []
    for tier, count in results:
        percentage = (count / total) * 100 if total > 0 else 0
        tiers.append({
            "tier": tier,
            "count": count,
            "percentage": round(percentage, 2),
            "pct_of_total": round(percentage, 2)
        })

    # Ensure all tiers are represented, even with zero counts
    all_tiers = [
        "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "INSUFFICIENT"
    ]
    tier_dict = {t["tier"]: t for t in tiers}
    for tier in all_tiers:
        if tier not in tier_dict:
            tiers.append({
                "tier": tier,
                "count": 0,
                "percentage": 0.0,
                "pct_of_total": 0.0
            })

    return {
        "tiers": tiers,
        "total": total,
        "registry_source_filter": registry_source
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
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

    # Add test data
    with SessionLocal() as db:
        test_data = [
            {"risk_tier": "TRUSTED_GENERAL", "registry_source": "source1"},
            {"risk_tier": "TRUSTED_GENERAL", "registry_source": "source2"},
            {"risk_tier": "TRUSTED_RESEARCH", "registry_source": "source1"},
            {"risk_tier": "ENTERPRISE_CONTROLLED", "registry_source": "source2"},
            {"risk_tier": "CAUTION_LIMITED", "registry_source": "source1"},
            {"risk_tier": "HIGH_RISK_ISOLATED", "registry_source": "source2"},
            {"risk_tier": "KNOWN_THREAT", "registry_source": "source1"},
            {"risk_tier": "INSUFFICIENT", "registry_source": "source2"},
            {"risk_tier": "TRUSTED_GENERAL", "registry_source": "source1"},
        ]
        for data in test_data:
            db.add(MCPServerRegistry(**data))
        db.commit()

    client = TestClient(app)

    # Test without filter
    response = client.get("/mcp/risk-tier/distribution")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tiers"]) == 7
    assert all(t["count"] >= 0 for t in data["tiers"])
    assert abs(sum(t["percentage"] for t in data["tiers"]) - 100) < 0.01
    assert data["total"] == sum(t["count"] for t in data["tiers"])

    # Test with filter
    response = client.get("/mcp/risk-tier/distribution?registry_source=source1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tiers"]) == 7
    assert all(t["count"] >= 0 for t in data["tiers"])
    assert abs(sum(t["percentage"] for t in data["tiers"]) - 100) < 0.01
    assert data["total"] == sum(t["count"] for t in data["tiers"])

    print("PASS")