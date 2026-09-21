from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")

class RiskTierThresholdResponse(BaseModel):
    days: int
    thresholds: Dict[str, float]
    counts: Dict[str, int]

@router.get("/risk/tier/threshold", response_model=RiskTierThresholdResponse)
async def get_risk_tier_thresholds(days: int, session: Session = Depends(get_session)):
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be positive")

    cutoff = datetime.utcnow() - timedelta(days=days)

    query = session.query(
        McpServerRegistry.risk_tier,
        func.percentile_cont(0.75).within_group(McpLlmAxisScore.p_top).label("p75_top"),
        func.count().label("count")
    ).join(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).filter(
        McpLlmAxisScore.scored_at >= cutoff
    ).group_by(
        McpServerRegistry.risk_tier
    ).all()

    thresholds = {tier: float(p75_top) for tier, p75_top, _ in query}
    counts = {tier: int(count) for tier, _, count in query}

    return {
        "days": days,
        "thresholds": thresholds,
        "counts": counts
    }

def test_risk_tier_thresholds():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        servers = [
            McpServerRegistry(
                server_id=f"server_{i}",
                risk_tier="LOW" if i % 3 == 0 else ("MEDIUM" if i % 3 == 1 else "HIGH"),
                org_id=1
            ) for i in range(9)
        ]
        session.add_all(servers)
        session.commit()

        # Create test scores
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        scores = [
            McpLlmAxisScore(
                server_id=f"server_{i}",
                p_top=0.1 + (i % 3) * 0.3,
                scored_at=now if i % 2 == 0 else yesterday
            ) for i in range(9)
        ]
        session.add_all(scores)
        session.commit()

        # Test the endpoint
        client = TestClient(app)
        response = client.get("/api/risk/tier/threshold?days=2")

        assert response.status_code == 200
        data = response.json()

        assert "days" in data and data["days"] == 2
        assert "thresholds" in data and all(tier in data["thresholds"] for tier in ["LOW", "MEDIUM", "HIGH"])
        assert "counts" in data and all(tier in data["counts"] for tier in ["LOW", "MEDIUM", "HIGH"])

        print("PASS")
    finally:
        session.close()
        engine.dispose()

if __name__ == "__main__":
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    test_risk_tier_thresholds()