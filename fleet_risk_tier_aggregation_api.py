from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from app.db import get_session
from app.models import MCPLLMAxisScore
from fastapi.testclient import TestClient

router = APIRouter()

class TierDistribution(BaseModel):
    tier: str
    count: int
    percentage: float
    avg_score: float

class TierDistributionResponse(BaseModel):
    tiers: List[TierDistribution]
    total_servers: int
    as_of: str

class TierTrendBucket(BaseModel):
    ts: str
    tier_counts: Dict[str, int]

class TierTrendResponse(BaseModel):
    buckets: List[TierTrendBucket]
    interval_hours: int

def get_risk_tier(score: float) -> str:
    if score < 0.1:
        return "TRUSTED_GENERAL"
    elif score < 0.2:
        return "TRUSTED_RESEARCH"
    elif score < 0.3:
        return "ENTERPRISE_CONTROLLED"
    elif score < 0.5:
        return "CAUTION_LIMITED"
    elif score < 0.7:
        return "HIGH_RISK_ISOLATED"
    elif score < 0.9:
        return "KNOWN_THREAT"
    else:
        return "INSUFFICIENT"

@router.get("/fleet/risk-tier-distribution", response_model=TierDistributionResponse)
async def get_risk_tier_distribution(
    decision_rule_version: Optional[str] = None,
    window_hours: int = 720,
    session=Depends(get_session)
):
    window = timedelta(hours=window_hours)
    cutoff = datetime.utcnow() - window

    query = session.query(
        MCPLLMAxisScore.server_id,
        MCPLLMAxisScore.p_top,
        MCPLLMAxisScore.decision_rule_version,
        MCPLLMAxisScore.scored_at
    ).filter(
        MCPLLMAxisScore.axis_name == 'overall_risk',
        MCPLLMAxisScore.scored_at >= cutoff
    )

    if decision_rule_version:
        query = query.filter(MCPLLMAxisScore.decision_rule_version == decision_rule_version)

    results = query.all()

    if not results:
        raise HTTPException(status_code=404, detail="No scores found in the given time window")

    tier_counts = {}
    tier_scores = {}
    total_servers = len(results)

    for server_id, score, _, _ in results:
        tier = get_risk_tier(score)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        tier_scores[tier] = tier_scores.get(tier, []) + [score]

    tiers = []
    for tier in [
        "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "INSUFFICIENT"
    ]:
        count = tier_counts.get(tier, 0)
        percentage = (count / total_servers) * 100 if total_servers > 0 else 0
        avg_score = sum(tier_scores.get(tier, [])) / len(tier_scores.get(tier, [])) if tier_scores.get(tier) else 0
        tiers.append({
            "tier": tier,
            "count": count,
            "percentage": round(percentage, 2),
            "avg_score": round(avg_score, 4)
        })

    return {
        "tiers": tiers,
        "total_servers": total_servers,
        "as_of": datetime.utcnow().isoformat()
    }

@router.get("/fleet/risk-tier-trend", response_model=TierTrendResponse)
async def get_risk_tier_trend(
    window_hours: int = 720,
    interval_hours: int = 24,
    session=Depends(get_session)
):
    window = timedelta(hours=window_hours)
    interval = timedelta(hours=interval_hours)
    cutoff = datetime.utcnow() - window

    query = session.query(
        MCPLLMAxisScore.server_id,
        MCPLLMAxisScore.p_top,
        MCPLLMAxisScore.scored_at
    ).filter(
        MCPLLMAxisScore.axis_name == 'overall_risk',
        MCPLLMAxisScore.scored_at >= cutoff
    ).order_by(MCPLLMAxisScore.scored_at)

    results = query.all()

    if not results:
        raise HTTPException(status_code=404, detail="No scores found in the given time window")

    current_time = cutoff
    buckets = []

    while current_time < datetime.utcnow():
        next_time = current_time + interval
        bucket_results = [r for r in results if current_time <= r.scored_at < next_time]

        tier_counts = {}
        for _, score, _ in bucket_results:
            tier = get_risk_tier(score)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        buckets.append({
            "ts": current_time.isoformat(),
            "tier_counts": tier_counts
        })

        current_time = next_time

    return {
        "buckets": buckets,
        "interval_hours": interval_hours
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker
    from app.models import MCPLLMAxisScore

    app = FastAPI()
    app.include_router(router)

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        test_data = [
            MCPLLMAxisScore(
                server_id=f"server_{i}",
                axis_name="overall_risk",
                p_top=0.05 + i * 0.01,
                decision_rule_version="v1",
                scored_at=datetime.utcnow() - timedelta(hours=i)
            ) for i in range(100)
        ]
        session.add_all(test_data)
        session.commit()

    client = TestClient(app)

    # Test distribution endpoint
    response = client.get("/fleet/risk-tier-distribution")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tiers"]) == 7
    assert data["total_servers"] > 0

    # Test trend endpoint
    response = client.get("/fleet/risk-tier-trend")
    assert response.status_code == 200
    data = response.json()
    assert len(data["buckets"]) > 0

    print("PASS")