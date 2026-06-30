from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from typing import Dict, Optional

router = APIRouter()

class RiskTierComparison(BaseModel):
    tier: str
    count: int
    percentage: float

class RiskTierComparisonResponse(BaseModel):
    comparison: Dict[str, RiskTierComparison]

def get_db_session() -> Session:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

@router.get("/risk-tier-comparison", response_model=RiskTierComparisonResponse)
async def get_risk_tier_comparison(db: Session = Depends(get_db_session)):
    from mcp_server_registry.models import Server

    # Get total count of servers
    total_count = db.execute(select(func.count(Server.id))).scalar()

    # Get count per risk tier
    risk_tier_counts = db.execute(
        select(
            Server.risk_tier,
            func.count(Server.id).label("count")
        ).group_by(Server.risk_tier)
    ).fetchall()

    # Calculate percentage for each tier
    comparison = {}
    for tier, count in risk_tier_counts:
        percentage = (count / total_count) * 100 if total_count else 0
        comparison[tier] = {
            "count": count,
            "percentage": round(percentage, 2)
        }

    return {"comparison": comparison}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from mcp_server_registry.models import Server
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    engine = create_engine("sqlite:///:memory:")
    from mcp_server_registry.models import Base
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed test data
    with SessionLocal() as db:
        for tier in ["low", "medium", "high", "critical", "unknown", "unassigned"]:
            db.add(Server(risk_tier=tier))
        db.commit()

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test endpoint
    response = client.get("/risk-tier-comparison")
    assert response.status_code == 200
    data = response.json()
    assert len(data["comparison"]) == 6
    print("PASS")