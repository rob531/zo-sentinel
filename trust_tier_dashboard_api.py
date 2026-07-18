from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class TrustTierBreakdown(BaseModel):
    tier: str
    count: int
    percentage: float

@router.get("/trust-tier/dashboard", response_model=List[TrustTierBreakdown])
async def get_trust_tier_breakdown(db: Session = Depends(get_session)):
    total_servers = db.query(func.count(MCPServerRegistry.id)).scalar()

    if total_servers == 0:
        return []

    results = db.query(
        MCPServerRegistry.risk_tier,
        func.count(MCPServerRegistry.id).label("count")
    ).group_by(
        MCPServerRegistry.risk_tier
    ).all()

    breakdown = []
    for tier, count in results:
        percentage = (count / total_servers) * 100
        breakdown.append({
            "tier": tier,
            "count": count,
            "percentage": round(percentage, 2)
        })

    return breakdown

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    client = TestClient(app)

    response = client.get("/trust-tier/dashboard")
    assert response.status_code == 200
    data = response.json()

    required_tiers = [
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "TRUSTED_ENTERPRISE",
        "TRUSTED_GOVERNMENT",
        "TRUSTED_MILITARY",
        "TRUSTED_FINANCIAL",
        "INSUFFICIENT"
    ]

    received_tiers = [item["tier"] for item in data]
    assert all(tier in received_tiers for tier in required_tiers)
    assert all(item["count"] > 0 for item in data)

    print("PASS")