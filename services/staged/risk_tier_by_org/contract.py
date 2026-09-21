from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List
from app.db import get_session
from app.models import McpServerRegistry
import requests
import json
from sqlalchemy.orm import Session

app = FastAPI()

class RiskTierDistribution(BaseModel):
    risk_tier: str
    count: int

class RiskTierResponse(BaseModel):
    org_id: str
    tiers: List[RiskTierDistribution]
    total_servers: int

def query_write_service(sql: str, params: dict = None) -> list:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params or {}}
    )
    response.raise_for_status()
    return response.json()["rows"]

@app.get("/api/risk/tiers_by_org", response_model=RiskTierResponse)
async def get_risk_tiers_by_org(org_id: str = Query(...), session: Session = Depends(get_session)):
    # Query MCP server registry for servers belonging to the org
    servers = session.query(McpServerRegistry).filter(McpServerRegistry.org_id == org_id).all()

    if not servers:
        raise HTTPException(status_code=404, detail="No servers found for the given organization")

    # Count servers by risk tier
    tier_counts = {}
    for server in servers:
        tier = server.risk_tier
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Prepare response
    tiers = [{"risk_tier": tier, "count": count} for tier, count in tier_counts.items()]
    response = {
        "org_id": org_id,
        "tiers": tiers,
        "total_servers": len(servers)
    }

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Insert test data
    with SessionLocal() as session:
        session.execute("""
        INSERT INTO McpServerRegistry (server_id, org_id, risk_tier)
        VALUES
            ('server1', 'org123', 'HIGH_RISK_ISOLATED'),
            ('server2', 'org123', 'CAUTION_LIMITED'),
            ('server3', 'org123', 'HIGH_RISK_ISOLATED')
        """)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tiers_by_org?org_id=org123")

    assert response.status_code == 200
    data = response.json()
    assert len(data["tiers"]) == 2
    assert data["tiers"][0]["risk_tier"] == "HIGH_RISK_ISOLATED"
    assert data["tiers"][0]["count"] == 2
    assert data["total_servers"] == 3

    print("PASS")