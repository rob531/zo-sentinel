from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Dict

from mcp_server_registry import ServerRegistry

router = APIRouter()

class RiskTierOverview(BaseModel):
    critical: int
    high: int
    medium: int
    low: int
    informational: int
    unknown: int

def get_db_session() -> Session:
    # This would be replaced with your actual session dependency in a real app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

@router.get("/risk-tier-overview", response_model=RiskTierOverview)
async def get_risk_tier_overview(db: Session = Depends(get_db_session)):
    # Count servers by risk tier
    stmt = (
        select(
            ServerRegistry.risk_tier,
            func.count(ServerRegistry.id).label("count")
        )
        .group_by(ServerRegistry.risk_tier)
    )
    result = db.execute(stmt).fetchall()

    # Initialize all tiers with 0 count
    overview = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "informational": 0,
        "unknown": 0
    }

    # Update counts from query results
    for tier, count in result:
        if tier in overview:
            overview[tier] = count

    return overview

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create in-memory database and test client
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from mcp_server_registry import Base
    Base.metadata.create_all(bind=engine)

    # Seed test data
    with SessionLocal() as db:
        from mcp_server_registry import ServerRegistry
        test_servers = [
            ServerRegistry(
                id=1, risk_tier="critical",
                hostname="server1", ip_address="192.168.1.1"
            ),
            ServerRegistry(
                id=2, risk_tier="high",
                hostname="server2", ip_address="192.168.1.2"
            ),
            ServerRegistry(
                id=3, risk_tier="medium",
                hostname="server3", ip_address="192.168.1.3"
            ),
            ServerRegistry(
                id=4, risk_tier="low",
                hostname="server4", ip_address="192.168.1.4"
            ),
            ServerRegistry(
                id=5, risk_tier="informational",
                hostname="server5", ip_address="192.168.1.5"
            ),
            ServerRegistry(
                id=6, risk_tier="unknown",
                hostname="server6", ip_address="192.168.1.6"
            ),
            ServerRegistry(
                id=7, risk_tier="critical",
                hostname="server7", ip_address="192.168.1.7"
            ),
        ]
        db.add_all(test_servers)
        db.commit()

    # Create test app and client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/risk-tier-overview")
    assert response.status_code == 200
    assert response.json() == {
        "critical": 2,
        "high": 1,
        "medium": 1,
        "low": 1,
        "informational": 1,
        "unknown": 1
    }
    print("PASS")