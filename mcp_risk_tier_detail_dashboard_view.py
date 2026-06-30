from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import List, Dict, Any

router = APIRouter()

class ServerDetail(BaseModel):
    server_id: int
    name: str
    risk_score: float
    last_assessed: str

class RiskTierDetail(BaseModel):
    tier: Dict[str, Dict[str, List[ServerDetail]]]

def get_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mcp_server_registry import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

@router.get("/mcp/risk-tier-detail", response_model=RiskTierDetail)
async def get_risk_tier_detail(tier: str, db: Session = Depends(get_db_session)):
    from mcp_server_registry import Server

    # Query servers in the specified tier
    stmt = select(Server).where(Server.risk_tier == tier)
    servers = db.execute(stmt).scalars().all()

    if not servers:
        raise HTTPException(status_code=404, detail="No servers found for the specified tier")

    # Prepare the response
    server_details = [
        {
            "server_id": server.id,
            "name": server.name,
            "risk_score": server.risk_score,
            "last_assessed": server.last_assessed.isoformat() if server.last_assessed else None
        }
        for server in servers
    ]

    return {"tier": {tier: {"servers": server_details}}}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from mcp_server_registry import Base, Server
    from datetime import datetime

    # Setup in-memory database and seed data
    db = get_db_session()
    Base.metadata.create_all(db.engine)

    # Seed data
    test_servers = [
        Server(
            name="Server1",
            risk_score=0.8,
            risk_tier="high",
            last_assessed=datetime.now()
        ),
        Server(
            name="Server2",
            risk_score=0.5,
            risk_tier="medium",
            last_assessed=datetime.now()
        ),
        Server(
            name="Server3",
            risk_score=0.9,
            risk_tier="high",
            last_assessed=datetime.now()
        )
    ]
    db.add_all(test_servers)
    db.commit()

    # Test the endpoint
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test high tier
    response = client.get("/mcp/risk-tier-detail?tier=high")
    assert response.status_code == 200
    assert len(response.json()["tier"]["high"]["servers"]) == 2

    # Test medium tier
    response = client.get("/mcp/risk-tier-detail?tier=medium")
    assert response.status_code == 200
    assert len(response.json()["tier"]["medium"]["servers"]) == 1

    print("PASS")