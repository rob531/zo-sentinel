from datetime import datetime, timedelta
from typing import List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import requests
from app.db import get_session
from app.models import McpRiskRegister

app = FastAPI()

class RiskTierEvolutionResponse(BaseModel):
    period_start: str
    period_end: str
    tiers: Dict[str, int]

class RiskTierEvolutionReport(BaseModel):
    report: List[RiskTierEvolutionResponse]

def get_risk_tier_evolution(window_hours: int = 168, bucket_minutes: int = 1440) -> List[Dict[str, Any]]:
    session = Depends(get_session)
    now = datetime.utcnow()
    window_start = now - timedelta(hours=window_hours)

    # Get all risk register entries within the window
    query = session.query(McpRiskRegister).filter(
        McpRiskRegister.computed_at >= window_start
    ).all()

    if not query:
        return []

    # Initialize buckets
    current_time = window_start
    buckets = []
    while current_time < now:
        period_end = current_time + timedelta(minutes=bucket_minutes)
        buckets.append({
            "period_start": current_time.isoformat() + "Z",
            "period_end": period_end.isoformat() + "Z",
            "tiers": {}
        })
        current_time = period_end

    # Count tiers per bucket
    for entry in query:
        for bucket in buckets:
            if bucket["period_start"] <= entry.computed_at.isoformat() + "Z" < bucket["period_end"]:
                tier = entry.risk_tier
                bucket["tiers"][tier] = bucket["tiers"].get(tier, 0) + 1
                break

    return buckets

@app.get("/reports/risk-tier-evolution", response_model=RiskTierEvolutionReport)
async def risk_tier_evolution_endpoint(window_hours: int = 168, bucket_minutes: int = 1440):
    try:
        report = get_risk_tier_evolution(window_hours, bucket_minutes)
        return {"report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_session.add_all([
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=6), risk_tier="TRUSTED_GENERAL"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=6), risk_tier="CAUTION_LIMITED"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=5), risk_tier="TRUSTED_GENERAL"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=4), risk_tier="CAUTION_LIMITED"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=4), risk_tier="CAUTION_LIMITED"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=3), risk_tier="TRUSTED_GENERAL"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=3), risk_tier="TRUSTED_GENERAL"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=2), risk_tier="TRUSTED_GENERAL"),
        McpRiskRegister(computed_at=datetime.utcnow() - timedelta(days=1), risk_tier="CAUTION_LIMITED"),
    ])
    test_session.commit()

    # Run the test
    result = get_risk_tier_evolution(window_hours=168, bucket_minutes=1440)
    assert isinstance(result, list), "Result is not a list"
    if result:
        print(result[0])
    print("PASS")