from fastapi import APIRouter, Depends, Query
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

class RiskDistributionResponse(BaseModel):
    tiers: List[RiskTierDistribution]
    total: int
    as_of: str

@router.get("/fleet/risk-distribution", response_model=RiskDistributionResponse)
def get_risk_distribution(
    session: Session = Depends(get_session),
    min_score: Optional[float] = Query(None)
):
    query = session.query(
        MCPServerRegistry.risk_tier,
        func.count(MCPServerRegistry.id).label('count')
    ).group_by(MCPServerRegistry.risk_tier)

    if min_score is not None:
        query = query.filter(MCPServerRegistry.trust_score >= min_score)

    results = query.all()
    total = sum(count for _, count in results)

    tiers = [
        RiskTierDistribution(
            tier=tier,
            count=count,
            percentage=(count / total) * 100 if total > 0 else 0.0
        )
        for tier, count in results
    ]

    return RiskDistributionResponse(
        tiers=tiers,
        total=total,
        as_of="now"
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import random

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestSession() as session:
        for i in range(50):
            tier = random.choice(["A", "B", "C", "D", "E", "F"])
            session.add(MCPServerRegistry(
                id=i,
                risk_tier=tier,
                trust_score=random.uniform(0, 100)
            ))
        session.commit()

    client = TestClient(app)
    response = client.get("/fleet/risk-distribution")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tiers"]) == 6
    assert sum(tier["count"] for tier in data["tiers"]) == 50
    print("PASS")