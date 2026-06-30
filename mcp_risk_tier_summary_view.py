from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Optional
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from mcp_server_registry import RiskTier, ServerRegistry

router = APIRouter()

class RiskTierSummary(BaseModel):
    count: int
    percentage: float

class RiskTierSummaryResponse(BaseModel):
    summary: Dict[str, RiskTierSummary]

def get_db_session() -> Session:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:", echo=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

@router.get("/risk-tier-summary", response_model=RiskTierSummaryResponse)
async def get_risk_tier_summary(db: Session = Depends(get_db_session)):
    # Calculate total count of servers
    total_count = db.execute(select(func.count()).select_from(ServerRegistry)).scalar()

    # Calculate count and percentage for each risk tier
    risk_tier_counts = db.execute(
        select(
            ServerRegistry.risk_tier,
            func.count(ServerRegistry.id).label("count")
        ).group_by(ServerRegistry.risk_tier)
    ).fetchall()

    summary = {}
    for tier, count in risk_tier_counts:
        percentage = (count / total_count) * 100 if total_count > 0 else 0
        summary[tier.value] = {"count": count, "percentage": round(percentage, 2)}

    return {"summary": summary}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry import Base

    # Setup in-memory database
    engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed test data
    db = SessionLocal()
    for tier in RiskTier:
        for _ in range(10):  # Add 10 servers for each risk tier
            db.add(ServerRegistry(risk_tier=tier))
    db.commit()

    # Create FastAPI app and test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/risk-tier-summary")
    assert response.status_code == 200
    data = response.json()
    assert len(data["summary"]) == 6  # All 6 risk tiers should be present
    print("PASS")