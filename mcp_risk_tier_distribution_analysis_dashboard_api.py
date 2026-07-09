from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry
from fastapi.testclient import TestClient
import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

router = APIRouter()

class RiskTierDistribution(BaseModel):
    tier_counts: Dict[str, int]

@router.get("/risk-tier-distribution-analysis", response_model=RiskTierDistribution, timeout=10)
async def get_risk_tier_distribution(db: Session = Depends(get_session)) -> Dict[str, int]:
    try:
        query = db.query(MCPServerRegistry.risk_tier).all()
        tier_counts = {}
        for tier, in query:
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        return {"tier_counts": tier_counts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        test_data = [
            MCPServerRegistry(risk_tier="Tier 1"),
            MCPServerRegistry(risk_tier="Tier 2"),
            MCPServerRegistry(risk_tier="Tier 3"),
            MCPServerRegistry(risk_tier="Tier 4"),
            MCPServerRegistry(risk_tier="Tier 5"),
            MCPServerRegistry(risk_tier="Tier 6"),
            MCPServerRegistry(risk_tier="Tier 1"),
            MCPServerRegistry(risk_tier="Tier 2"),
            MCPServerRegistry(risk_tier="Tier 3"),
            MCPServerRegistry(risk_tier="Tier 4"),
            MCPServerRegistry(risk_tier="Tier 5"),
            MCPServerRegistry(risk_tier="Tier 6"),
        ]
        db.add_all(test_data)
        db.commit()

    client = TestClient(app)
    response = client.get("/risk-tier-distribution-analysis")
    assert response.status_code == 200
    assert response.json() == {
        "tier_counts": {
            "Tier 1": 2,
            "Tier 2": 2,
            "Tier 3": 2,
            "Tier 4": 2,
            "Tier 5": 2,
            "Tier 6": 2,
        }
    }
    print("PASS")